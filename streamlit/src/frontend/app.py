"""Streamlit dashboard for PawChain Capital credit insights."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import json
import zipfile
import base64

import streamlit as st
from streamlit_lottie import st_lottie

from components.chatbot import render_chatbot_page
from components.intro import render_intro_page
from components.mcp_tools import render_mcp_tools_page
from components.navigation import render_navigation
from components.wallet import render_wallet_page


def _load_dotlottie_animation_data(filepath: str) -> dict | None:
    """Extract the first animation JSON from a .lottie (zip) container.

    Returns a Python dict suitable for lottie-web's `animationData`.
    """
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            # Try manifest.json for declared animation path
            manifest_json = None
            try:
                manifest_bytes = zf.read("manifest.json")
                manifest_json = json.loads(manifest_bytes.decode("utf-8"))
            except Exception:
                manifest_json = None

            candidate_path: str | None = None
            if isinstance(manifest_json, dict):
                animations = manifest_json.get("animations")
                if isinstance(animations, list) and animations:
                    for entry in animations:
                        if isinstance(entry, dict):
                            # dotLottie spec often uses `path` like "animations/xxx.json"
                            path = entry.get("path") or (entry.get("animation") or {}).get("path")
                            if isinstance(path, str) and path in zf.namelist():
                                candidate_path = path
                                break

            # Fallback: pick the first JSON file (prefer under animations/)
            if not candidate_path:
                names = zf.namelist()
                # Prefer files under animations/
                anim_jsons = [n for n in names if n.endswith(".json") and "animations/" in n]
                if not anim_jsons:
                    anim_jsons = [n for n in names if n.endswith(".json")]
                candidate_path = anim_jsons[0] if anim_jsons else None

            if not candidate_path:
                return None

            data_bytes = zf.read(candidate_path)
            return json.loads(data_bytes.decode("utf-8"))
    except Exception:
        return None


def _load_lottie_any(filepath: str) -> dict | None:
    """Load animation data from a .lottie zip or plain JSON file.

    Returns a Python dict suitable for `st_lottie(animation_data=...)`.
    """
    # First, try reading as dotLottie zip
    data = _load_dotlottie_animation_data(filepath)
    if data:
        return data
    # Fallback: try plain JSON
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _show_lottie_splash_streamlit(animation_data: dict, duration_ms: int = 5000) -> None:
    """Show a splash using streamlit-lottie, then hide after a timeout.

    Uses a placeholder and blocks for a short duration before clearing.
    """
    if not st.session_state.get("splash_shown"):
        st.session_state["splash_shown"] = True
        ph = st.empty()
        with ph.container():
            # Render the animation large; adjust height as needed
            st_lottie(animation_data, height=800, key="startup-splash")
        import time
        time.sleep(max(0, int(duration_ms / 1000)))
        ph.empty()


def _read_file_base64(filepath: str) -> str | None:
    """Return base64-encoded bytes for the given file path, or None on error."""
    try:
        with open(filepath, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return None


def _show_dotlottie_zip_splash_once(dotlottie_base64: str, background: str = "#000000", force_hide_after_ms: int = 6000) -> None:
    """Render a full-screen splash using the official dotlottie player (zip-aware).

    Loads the .lottie zip via a data URI so embedded assets are preserved.
    Shows once per session, hides on 'complete' or after a timeout fallback.
    """
    if not st.session_state.get("splash_shown"):
        st.session_state["splash_shown"] = True
        html = f"""
<style>
#splash-overlay {{
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  background: {background};
  z-index: 999999;
  display: flex;
  align-items: center;
  justify-content: center;
}}
 dotlottie-player#splash-player {{
  width: min(75vw, 1000px);
  height: min(75vh, 1000px);
  background: transparent;
}}
</style>
<div id=\"splash-overlay\"><dotlottie-player id=\"splash-player\" autoplay loop=\"false\"></dotlottie-player></div>
<script src=\"https://unpkg.com/@dotlottie/player-component@latest/dist/dotlottie-player.js\"></script>
<script>
  const b64 = \"{dotlottie_base64}\";
  const dataUrl = "data:application/dotlottie+zip;base64," + b64;
  const player = document.getElementById('splash-player');
  player.setAttribute('src', dataUrl);
  function hideOverlay() {{
    const overlay = document.getElementById('splash-overlay');
    overlay.style.transition = 'opacity 400ms ease';
    overlay.style.opacity = '0';
    setTimeout(() => {{ overlay.remove(); }}, 420);
  }}
  player.addEventListener('complete', hideOverlay);
  setTimeout(hideOverlay, {force_hide_after_ms});
</script>
        """
        st.markdown(html, unsafe_allow_html=True)


dotenv_spec = importlib.util.find_spec("dotenv")
if dotenv_spec is not None:  # pragma: no cover - imported at runtime when available
    from dotenv import load_dotenv  # type: ignore[import]
else:  # pragma: no cover - dependency optional for linting
    load_dotenv = None  # type: ignore[assignment]


if load_dotenv:  # pragma: no cover - executed at runtime
    env_path = Path(__file__).resolve().parents[3] / ".env"
    load_dotenv(env_path)


st.set_page_config(page_title="Credit Dashboard", page_icon="🐶", layout="wide")

# Show full-screen Lottie splash on first load via streamlit-lottie
_DOTLOTTIE_PATH = "/Users/abdulaaqib/Developer/Github/arc_encode_hack/streamlit/src/frontend/lottie_files/Abstract _Orb.json"
_anim = _load_lottie_any(_DOTLOTTIE_PATH)
if _anim:
    _show_lottie_splash_streamlit(_anim, duration_ms=6000)

active_page = render_navigation()

if active_page == "Intro":
    render_intro_page()
elif active_page == "Chatbot":
    render_chatbot_page()
elif active_page == "Wallet":
    render_wallet_page()
else:
    render_mcp_tools_page()

