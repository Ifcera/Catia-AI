"""Arayüz denetleyicisi: agent ve CATIA işlerini arka plan iş parçacıklarında çalıştırır, sonuçları Qt sinyalleriyle bildirir.

Arayüz iş parçacığı hiçbir zaman COM ya da ağ çağrısı yapmaz; böylece pencere donmaz.
Sinyaller başka iş parçacıklarından yayınlanır; Qt bunları alıcının (ana pencere) iş parçacığına kuyruklar.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..agent import Agent, AgentConfig, AgentEvent, TurnResult, get_api_key, make_provider
from ..agent.claude_code import ClaudeCodeAgent, claude_status
from ..catia.dialogs import catia_pids
from ..tools import CatiaTools
from .labels import selection_context
from ..i18n import t

BACKENDS = {
    "claude_code": "Claude Code (abonelik)",
    "anthropic": "Claude API (anahtar)",
    "openai": "ChatGPT (anahtar)",
    "gemini": "Gemini (anahtar)",
}
CLAUDE_CODE_MODELS = ["", "opus", "sonnet", "fable"]  # "" = Claude Code varsayılanı


def _thread(fn, *a):
    t = threading.Thread(target=fn, args=a, daemon=True)
    t.start()
    return t


class AgentController(QObject):
    event = Signal(object)            # AgentEvent
    turn_finished = Signal(object)    # TurnResult
    connected = Signal(dict)          # {"ok", "backend", "label", "detail", "model"}
    catia_status = Signal(bool)
    models_listed = Signal(str, list)  # backend, modeller
    notice = Signal(str, str)         # (tür: info|error|ok, metin)
    busy_changed = Signal(bool)
    user_attachment = Signal(list, str)  # görüntü yolları, seçim bağlamı

    def __init__(self, tools_factory=CatiaTools):
        super().__init__()
        self._tools_factory = tools_factory
        self.tools: CatiaTools | None = None
        self.agent = None
        self.backend: str | None = None
        self.workspace: Path | None = None
        self.busy = False

    # ------------------------------------------------------------ durum
    def poll_catia(self) -> None:
        _thread(lambda: self.catia_status.emit(bool(catia_pids())))

    def list_models(self, backend: str) -> None:
        def work():
            if backend == "claude_code":
                self.models_listed.emit(backend, CLAUDE_CODE_MODELS)
                return
            key = get_api_key(backend)
            if not key:
                self.models_listed.emit(backend, [])
                return
            try:
                self.models_listed.emit(backend, make_provider(backend, api_key=key).list_models())
            except Exception as exc:  # noqa: BLE001
                self.notice.emit("error", t("Model listesi alınamadı: {e}").format(e=exc.__class__.__name__))
                self.models_listed.emit(backend, [])
        _thread(work)

    # ---------------------------------------------------------- bağlantı
    def connect_backend(self, backend: str, model: str | None, effort: str, workspace: str) -> None:
        if self.busy:
            return
        _thread(self._connect, backend, model or None, effort, Path(workspace))

    def _emit_event(self, e: AgentEvent) -> None:
        self._log(e)
        self.event.emit(e)

    def _log(self, e: AgentEvent) -> None:
        """Olay günlüğü (çalışma klasörü/logs/*.jsonl): hata ayıklama için; metin akışı parçaları ve görüntü verisi yazılmaz."""
        if self.workspace is None or e.kind in ("text_delta", "thinking_delta"):
            return
        try:
            log_dir = self.workspace / "logs"
            log_dir.mkdir(exist_ok=True)
            rec = {"t": time.strftime("%H:%M:%S"), "kind": e.kind, **e.data}
            with open(log_dir / f"oturum_{time.strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str)[:4000] + "\n")
        except OSError:
            pass

    def _connect(self, backend, model, effort, workspace: Path):
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            if self.tools is None:
                self.tools = self._tools_factory(workspace=workspace)
            else:
                self.tools.set_workspace(workspace)
            self.workspace = workspace
            catia_ok = self.tools.run(lambda s: s.is_connected())
            self.catia_status.emit(bool(catia_ok))
            if not catia_ok:
                self.connected.emit({"ok": False, "backend": backend, "detail": t("CATIA açık değil. CATIA V5'i açıp tekrar bağlanın.")})
                return
            self._close_agent()
            if backend == "claude_code":
                st = claude_status()
                if not st["installed"]:
                    raise RuntimeError(t("Claude Code kurulu değil (claude komutu bulunamadı)."))
                if not st["logged_in"]:
                    raise RuntimeError(t("Claude Code'da oturum açık değil. Terminalde 'claude' çalıştırıp giriş yapın."))
                self.agent = ClaudeCodeAgent(self.tools, workspace, on_event=self._emit_event, model=model, effort=effort)
                label = f"Claude Code · {st.get('plan') or t('abonelik')}"
                model_name = model or t("varsayılan")
            else:
                if not get_api_key(backend):
                    raise RuntimeError(t("{p} için kayıtlı API anahtarı yok. 'Anahtar…' ile girin.").format(p=t(BACKENDS[backend])))
                self.agent = Agent(self.tools, AgentConfig(provider=backend, model=model, effort=effort), on_event=self._emit_event)
                label = t(BACKENDS[backend]).split(" (")[0]
                model_name = self.agent.model
            self.backend = backend
            self.connected.emit({"ok": True, "backend": backend, "label": label, "model": model_name,
                                 "detail": f"{label} · {model_name}"})
        except Exception as exc:  # noqa: BLE001
            self.agent, self.backend = None, None
            self.connected.emit({"ok": False, "backend": backend, "detail": str(exc)})

    def _close_agent(self):
        if self.agent is not None and hasattr(self.agent, "close"):
            try:
                self.agent.close()
            except Exception:  # noqa: BLE001
                pass
        self.agent = None

    # -------------------------------------------------------------- tur
    def send(self, text: str, attach_view: bool, attach_selection: bool, images: list[str] | None = None) -> None:
        if self.busy or self.agent is None:
            return
        self._set_busy(True)
        _thread(self._send, text, attach_view, attach_selection, list(images or []))

    def _set_busy(self, v: bool):
        self.busy = v
        self.busy_changed.emit(v)

    def _send(self, text, attach_view, attach_selection, uploads):
        try:
            images, context = list(uploads), None
            if attach_view:
                r = self.tools.call("screenshot", views=["current"], max_size=768)
                if r.ok:
                    images += [str(p) for p in r.images]
                else:
                    self.notice.emit("error", t("Görünüm alınamadı: {e}").format(e=r.data.get("error")))
            if attach_selection:
                r = self.tools.call("get_selection")
                context = selection_context(r.data)
                if context is None:
                    self.notice.emit("info", t("CATIA'da seçili öğe yok; seçim eklenmedi."))
            self.user_attachment.emit(images, context or "")
            result = self.agent.send(text, images=images or None, context=context)
        except Exception as exc:  # noqa: BLE001
            result = TurnResult("error", error=f"{exc.__class__.__name__}: {exc}")
            self.event.emit(AgentEvent("error", {"message": result.error}))
        finally:
            self._set_busy(False)
        self.turn_finished.emit(result)

    def stop(self) -> None:
        if self.agent is not None:
            _thread(self.agent.stop)

    def undo_last(self) -> None:
        if self.agent is None or self.busy:
            return

        def work():
            self._set_busy(True)
            try:
                res = self.agent.undo_last_message()
                if not res.get("group"):
                    self.notice.emit("info", t("Geri alınacak bir değişiklik yok."))
                elif res.get("errors"):
                    self.notice.emit("error", t("'{g}' kısmen geri alındı; hatalar: {e}").format(g=res["group"], e=res["errors"][:3]))
                else:
                    self.notice.emit("ok", t("'{g}' geri alındı ({n} işlem).").format(g=res["group"], n=len(res["undone"])))
            finally:
                self._set_busy(False)
        _thread(work)

    def new_conversation(self) -> None:
        if self.agent is not None and not self.busy:
            self.agent.reset()
            self.notice.emit("info", t("Yeni konuşma başladı (CATIA'daki model olduğu gibi kaldı)."))

    def shutdown(self) -> None:
        if self.agent is not None:
            try:
                self.agent.stop()
            except Exception:  # noqa: BLE001
                pass
        self._close_agent()
        if self.tools is not None:
            self.tools.close()
