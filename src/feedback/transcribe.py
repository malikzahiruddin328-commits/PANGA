"""Best-effort speech-to-text for the point-and-talk feedback widget.

Uses SpeechRecognition's free Google Web Speech endpoint - no API key
needed (nothing else in this project has one configured for speech), but
it's an unofficial, rate-limited, internet-dependent service, not a
production-grade transcription API. Callers must treat a returned error as
"ask the user to type it instead", never surface it as a bug - the text box
underneath the recorder is always the fallback.
"""

import speech_recognition as sr


def transcribe_audio(audio_file) -> tuple[str | None, str | None]:
    """audio_file is any file-like object containing WAV audio (e.g. the
    UploadedFile st.audio_input returns). Returns (transcript, error) -
    exactly one of the two is None."""
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(audio_file) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio), None
    except sr.UnknownValueError:
        return None, "Couldn't make out any speech in that recording - try again, or just type it below."
    except sr.RequestError:
        return None, "Couldn't reach the free transcription service (it needs internet) - type your note below instead."
