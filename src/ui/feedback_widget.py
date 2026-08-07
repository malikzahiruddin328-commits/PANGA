"""Shared 'point and talk' feedback widget (Zahir's request 2026-07-30): one
voice-or-text note per screen, so a later Claude Code session can read
exactly what to change without him typing it out. Rendered once per tab,
tagged with that tab's section key - "point" is just being on the screen in
question, "talk" is the recording itself.

Kept out of app.py (already large) per the code-organization guidance to
move shared UI helpers into their own module.
"""

import streamlit as st

from feedback.transcribe import transcribe_audio
from feedback.ui_feedback import add_feedback


def render_feedback_widget(section_key: str) -> None:
    text_key = f"fb_text_{section_key}"
    transcribed_marker_key = f"fb_transcribed_for_{section_key}"
    clear_pending_key = f"fb_clear_pending_{section_key}"

    # A widget's own session_state key can only be reassigned BEFORE that
    # widget is instantiated in a given script run - trying to clear the
    # text box right after the Save button handler (same run, widget
    # already created above) raises StreamlitAPIException. So "Save"
    # below only sets this flag + reruns; the actual clear happens here,
    # at the top of the *next* run, before st.text_area exists yet.
    if st.session_state.pop(clear_pending_key, False):
        st.session_state[text_key] = ""

    with st.expander("Leave feedback on this screen", icon=":material/mic:"):
        st.markdown(
            "Record a quick voice note about what should change here, or just "
            "type it - no need to open Claude Code first. Voice notes are "
            "transcribed automatically (needs internet); check the text below "
            "before saving in case it misheard something."
        )

        audio = st.audio_input("Record", key=f"fb_audio_{section_key}", label_visibility="collapsed")

        if audio is not None and st.session_state.get(transcribed_marker_key) != audio.file_id:
            st.session_state[transcribed_marker_key] = audio.file_id
            transcript, error = transcribe_audio(audio)
            if transcript:
                st.session_state[text_key] = transcript
            elif error:
                st.warning(error, icon=":material/warning:")

        note = st.text_area(
            "Feedback note", value=st.session_state.get(text_key, ""),
            key=text_key, label_visibility="collapsed",
            placeholder="What should change on this screen?",
        )
        if st.button("Save feedback", key=f"fb_save_{section_key}", disabled=not note.strip()):
            add_feedback(section_key, note.strip())
            st.session_state[clear_pending_key] = True
            st.session_state.pop(transcribed_marker_key, None)
            st.toast("Saved - I'll read this next session.", icon=":material/check_circle:")
            st.rerun()
