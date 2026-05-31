"""
╔══════════════════════════════════════════════════════════════╗
║          TEXT-TO-SONG NOTEPAD  —  v1.0                       ║
║                                                              ║
║  Converts any typed text into a musical sequence.            ║
║  Each character maps deterministically to a musical note     ║
║  via ASCII/Unicode mod 12. Spaces become rests.              ║
║                                                              ║
║  Requirements:                                               ║
║    • Python 3.10+  (tested on 3.14)                          ║
║    • Built-in only: tkinter, wave, struct, threading,        ║
║      tempfile, math, os, colorsys                            ║
║    • winsound  (Windows built-in, pre-installed)             ║
║                                                              ║
║  pip installs:  NONE required on Windows.                    ║
║  On macOS/Linux: pip install simpleaudio                     ║
║  (see cross-platform note inside AudioEngine)                ║
╚══════════════════════════════════════════════════════════════╝
"""

# ─────────────────────────────────────────────────────────────
#  Standard-library imports
# ─────────────────────────────────────────────────────────────
import os
import sys
import math
import wave
import struct
import tempfile
import threading
import colorsys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# ─────────────────────────────────────────────────────────────
#  Cross-platform audio backend selection
#  Priority: winsound (Windows) → simpleaudio → fallback beep
# ─────────────────────────────────────────────────────────────
AUDIO_BACKEND = None

if sys.platform == "win32":
    try:
        import winsound
        AUDIO_BACKEND = "winsound"
    except ImportError:
        pass

if AUDIO_BACKEND is None:
    try:
        import simpleaudio as sa
        AUDIO_BACKEND = "simpleaudio"
    except ImportError:
        pass

# If neither backend is available we still run but warn the user at startup.


# ══════════════════════════════════════════════════════════════
#  MUSIC THEORY  —  note frequencies & text → note mapping
# ══════════════════════════════════════════════════════════════

# Chromatic scale note names (12 semitones)
NOTE_NAMES = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]

# Concert-pitch reference: A4 = 440 Hz
A4_FREQ = 440.0
A4_OCTAVE = 4
A4_SEMITONE = 0   # index 0 in NOTE_NAMES corresponds to 'A'


def semitone_to_freq(semitone_index: int, octave: int) -> float:
    """
    Return the frequency (Hz) for a given semitone index (0–11) and octave.
    Uses equal temperament relative to A4 = 440 Hz.

    Distance in semitones from A4:
        d = (octave - 4) * 12 + (semitone_index - 0)
    """
    semitones_from_a4 = (octave - A4_OCTAVE) * 12 + (semitone_index - A4_SEMITONE)
    return A4_FREQ * (2 ** (semitones_from_a4 / 12.0))


def char_to_note(char: str, base_octave: int, octave_range: int) -> dict | None:
    """
    Map a single character to a musical note descriptor, or None for a rest.

    Algorithm
    ─────────
    • Space (and similar whitespace) → rest  (returns None)
    • All other chars:
        semitone = ord(char) % 12          (deterministic 0–11 mapping)
        octave   = base_octave + (ord(char) // 12) % octave_range

    Returns a dict: { "name": str, "freq": float, "octave": int }
    """
    if char in (" ", "\t", "\r"):
        return None   # rest / pause

    code = ord(char)
    semitone = code % 12
    octave_offset = (code // 12) % octave_range
    octave = base_octave + octave_offset

    freq = semitone_to_freq(semitone, octave)
    return {
        "name": NOTE_NAMES[semitone],
        "freq": freq,
        "octave": octave,
    }


# ══════════════════════════════════════════════════════════════
#  AUDIO ENGINE  —  WAV synthesis & platform playback
# ══════════════════════════════════════════════════════════════

SAMPLE_RATE = 44100    # samples per second
AMPLITUDE   = 28000    # 16-bit signed max = 32767; leave headroom

def _adsr_envelope(num_samples: int, sample_rate: int,
                   attack_ratio=0.05, decay_ratio=0.1,
                   sustain_level=0.75, release_ratio=0.2) -> list[float]:
    """
    Generate an ADSR amplitude envelope as a list of float multipliers [0.0–1.0].
    This makes notes sound musical rather than harsh square clicks.
    """
    attack  = int(num_samples * attack_ratio)
    decay   = int(num_samples * decay_ratio)
    release = int(num_samples * release_ratio)
    sustain = num_samples - attack - decay - release

    envelope = []
    # Attack: linear ramp up
    for i in range(attack):
        envelope.append(i / max(attack, 1))
    # Decay: ramp down to sustain level
    for i in range(decay):
        envelope.append(1.0 - (1.0 - sustain_level) * (i / max(decay, 1)))
    # Sustain: constant
    for _ in range(max(sustain, 0)):
        envelope.append(sustain_level)
    # Release: ramp down to zero
    for i in range(release):
        envelope.append(sustain_level * (1.0 - i / max(release, 1)))

    return envelope


def synthesise_note(freq: float, duration_ms: int,
                    sample_rate: int = SAMPLE_RATE,
                    amplitude: int = AMPLITUDE,
                    waveform: str = "sine") -> bytes:
    """
    Synthesise a single note as raw 16-bit signed PCM bytes.

    Supported waveforms
    ───────────────────
    • "sine"     — pure, smooth tone
    • "triangle" — slightly hollow, flute-like
    • "sawtooth" — bright, buzzy (adds harmonic richness)

    An ADSR envelope is applied to every note to avoid clicks.
    """
    num_samples = int(sample_rate * duration_ms / 1000)
    envelope = _adsr_envelope(num_samples, sample_rate)

    raw = []
    for i in range(num_samples):
        t = i / sample_rate          # time in seconds
        phase = 2.0 * math.pi * freq * t

        if waveform == "sine":
            sample = math.sin(phase)
        elif waveform == "triangle":
            # Triangle: sawtooth folded
            sample = 2.0 * abs(2.0 * (t * freq - math.floor(t * freq + 0.5))) - 1.0
        elif waveform == "sawtooth":
            sample = 2.0 * (t * freq - math.floor(0.5 + t * freq))
        else:
            sample = math.sin(phase)   # fallback

        value = int(sample * amplitude * envelope[i])
        value = max(-32767, min(32767, value))   # clamp to 16-bit range
        raw.append(struct.pack("<h", value))     # little-endian signed short

    return b"".join(raw)


def synthesise_rest(duration_ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Return silence (zero-amplitude PCM) for the given duration."""
    num_samples = int(sample_rate * duration_ms / 1000)
    return b"\x00\x00" * num_samples   # 16-bit zero samples


def build_wav_bytes(pcm_data: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """
    Wrap raw 16-bit mono PCM data in a valid RIFF/WAV header.
    Returns the complete WAV file as bytes (ready to write to disk).
    """
    num_channels    = 1
    bits_per_sample = 16
    byte_rate       = sample_rate * num_channels * bits_per_sample // 8
    block_align     = num_channels * bits_per_sample // 8
    data_size       = len(pcm_data)
    chunk_size      = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,                  # PCM sub-chunk size
        1,                   # audio format: PCM
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_data


class AudioEngine:
    """
    Converts a sequence of note descriptors into a WAV file and plays it.

    Thread-safe: call play_sequence() from any thread; it spawns its own
    internal thread so the Tkinter UI is never blocked.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate   = sample_rate
        self._stop_event   = threading.Event()
        self._play_thread  = None
        self._temp_path    = None  # path to current temp WAV file

    # ── Public API ───────────────────────────────────────────

    def play_sequence(self, notes: list, duration_ms: int,
                      waveform: str = "sine",
                      on_done=None, on_error=None):
        """
        Synthesise `notes` (list of char_to_note() dicts or None for rests)
        and play the resulting WAV asynchronously.

        Callbacks
        ─────────
        on_done()         — called on the worker thread when playback ends
        on_error(msg:str) — called on the worker thread if something fails
        """
        self.stop()   # cancel any previous playback

        def _worker():
            try:
                pcm = self._build_pcm(notes, duration_ms, waveform)
                wav = build_wav_bytes(pcm, self.sample_rate)
                self._play_wav(wav)
            except Exception as exc:
                if on_error:
                    on_error(str(exc))
                return
            if on_done and not self._stop_event.is_set():
                on_done()

        self._stop_event.clear()
        self._play_thread = threading.Thread(target=_worker, daemon=True)
        self._play_thread.start()

    def stop(self):
        """Signal the current playback to stop as soon as possible."""
        self._stop_event.set()
        # winsound has no stop API; we let the thread finish on its own.
        # simpleaudio wave objects support stop().
        if hasattr(self, "_sa_wave_obj") and self._sa_wave_obj:
            try:
                self._sa_wave_obj.stop()
            except Exception:
                pass
        if self._temp_path and os.path.exists(self._temp_path):
            try:
                os.remove(self._temp_path)
            except OSError:
                pass
        self._temp_path = None

    # ── Internal helpers ─────────────────────────────────────

    def _build_pcm(self, notes: list, duration_ms: int, waveform: str) -> bytes:
        """Concatenate synthesised PCM for every note/rest in the sequence."""
        segments = []
        for note in notes:
            if self._stop_event.is_set():
                break
            if note is None:
                # rest
                segments.append(synthesise_rest(duration_ms, self.sample_rate))
            else:
                segments.append(
                    synthesise_note(note["freq"], duration_ms,
                                    self.sample_rate, AMPLITUDE, waveform)
                )
        return b"".join(segments)

    def _play_wav(self, wav_bytes: bytes):
        """Write WAV to a temp file and hand off to the OS audio backend."""
        # Write to temp file (winsound and simpleaudio both need a file or bytes)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            self._temp_path = f.name

        try:
            if AUDIO_BACKEND == "winsound":
                # SND_FILENAME | SND_ASYNC: non-blocking OS-level playback
                winsound.PlaySound(self._temp_path,
                                   winsound.SND_FILENAME | winsound.SND_ASYNC)
                # We block our *worker* thread (not UI) until done or stopped
                # by polling the stop event.  winsound's async plays in the OS.
                # Use SND_SYNC + wait in thread instead so stop() can interrupt:
                winsound.PlaySound(None, winsound.SND_PURGE)  # reset
                winsound.PlaySound(self._temp_path, winsound.SND_FILENAME)

            elif AUDIO_BACKEND == "simpleaudio":
                with wave.open(self._temp_path, "rb") as wf:
                    raw = wf.readframes(wf.getnframes())
                self._sa_wave_obj = sa.play_buffer(
                    raw, 1, 2, self.sample_rate
                )
                self._sa_wave_obj.wait_done()

            else:
                # No audio backend: emit a console warning (UI already warned)
                print("[AudioEngine] No audio backend available. "
                      "Install 'simpleaudio' via pip.", file=sys.stderr)

        finally:
            # Clean up temp file after playback
            try:
                if self._temp_path and os.path.exists(self._temp_path):
                    os.remove(self._temp_path)
                    self._temp_path = None
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════
#  TKINTER UI  —  Text-to-Song Notepad
# ══════════════════════════════════════════════════════════════

# ── Colour palette ────────────────────────────────────────────
THEME = {
    "bg":          "#1A1A2E",   # deep navy background
    "bg_panel":    "#16213E",   # slightly lighter panel
    "bg_text":     "#0F3460",   # text area background
    "accent":      "#E94560",   # vivid rose accent
    "accent2":     "#533483",   # muted purple secondary
    "fg":          "#E8E8F0",   # primary foreground text
    "fg_muted":    "#8888AA",   # muted label text
    "border":      "#2A2A4E",   # subtle border
    "note_bg":     "#0D0D1A",   # note display background
    "btn_active":  "#C73652",   # button pressed state
    "slider_bg":   "#2E2E50",
}

FONT_MONO  = ("Courier New", 11)
FONT_UI    = ("Segoe UI",    10)
FONT_TITLE = ("Segoe UI",    13, "bold")
FONT_LABEL = ("Segoe UI",     9)
FONT_BTN   = ("Segoe UI",    11, "bold")
FONT_NOTE  = ("Courier New", 10)


class Notepad(tk.Tk):
    """
    Main application window.

    Layout (top → bottom)
    ──────────────────────
    ┌─────────────────────────────────────┐
    │  Title bar                          │
    ├─────────────────────────────────────┤
    │  ScrolledText (writing area)        │
    ├─────────────────────────────────────┤
    │  Control panel                      │
    │    [Tempo slider] [Octave dropdown] │
    │    [Waveform dropdown]              │
    │    [Convert to Song ▶]  [■ Stop]   │
    ├─────────────────────────────────────┤
    │  Status / note-stream display       │
    └─────────────────────────────────────┘
    """

    def __init__(self):
        super().__init__()

        self.title("Text-to-Song Notepad")
        self.configure(bg=THEME["bg"])
        self.geometry("820x680")
        self.minsize(600, 500)

        # Shared state
        self._is_playing = False
        self._engine     = AudioEngine()

        self._build_ui()
        self._check_audio_backend()

    # ── UI construction ──────────────────────────────────────

    def _build_ui(self):
        """Assemble all widgets."""
        self._build_header()
        self._build_text_area()
        self._build_controls()
        self._build_status_bar()

    def _build_header(self):
        """Decorative title banner."""
        hdr = tk.Frame(self, bg=THEME["bg_panel"],
                       highlightbackground=THEME["border"], highlightthickness=1)
        hdr.pack(fill=tk.X, padx=0, pady=0)

        tk.Label(
            hdr,
            text="♪  Text-to-Song Notepad",
            font=FONT_TITLE,
            bg=THEME["bg_panel"],
            fg=THEME["accent"],
            pady=10,
        ).pack(side=tk.LEFT, padx=16)

        tk.Label(
            hdr,
            text="Type anything. Press Convert. Hear music.",
            font=FONT_LABEL,
            bg=THEME["bg_panel"],
            fg=THEME["fg_muted"],
        ).pack(side=tk.LEFT, padx=4)

        # Backend badge
        backend_txt = f"Audio: {AUDIO_BACKEND or 'none'}"
        tk.Label(
            hdr,
            text=backend_txt,
            font=FONT_LABEL,
            bg=THEME["bg_panel"],
            fg=THEME["fg_muted"],
        ).pack(side=tk.RIGHT, padx=16)

    def _build_text_area(self):
        """Large scrollable writing area."""
        frame = tk.Frame(self, bg=THEME["bg"],
                         highlightbackground=THEME["border"], highlightthickness=1)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 4))

        self.text_area = scrolledtext.ScrolledText(
            frame,
            wrap=tk.WORD,
            font=FONT_MONO,
            bg=THEME["bg_text"],
            fg=THEME["fg"],
            insertbackground=THEME["accent"],      # cursor colour
            selectbackground=THEME["accent2"],
            selectforeground=THEME["fg"],
            relief=tk.FLAT,
            padx=14,
            pady=12,
            undo=True,
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)

        # Placeholder hint text
        placeholder = (
            "Start typing here…\n\n"
            "Every character becomes a musical note.\n"
            "Spaces create rests. Enjoy the melody!\n\n"
            "Example: 'Hello, World! 1234'"
        )
        self.text_area.insert(tk.END, placeholder)
        self.text_area.config(fg=THEME["fg_muted"])

        def _clear_placeholder(event):
            if self.text_area.cget("fg") == THEME["fg_muted"]:
                self.text_area.delete("1.0", tk.END)
                self.text_area.config(fg=THEME["fg"])

        self.text_area.bind("<FocusIn>", _clear_placeholder)

    def _build_controls(self):
        """Control panel with all configuration widgets and action buttons."""
        panel = tk.Frame(self, bg=THEME["bg_panel"],
                         highlightbackground=THEME["border"], highlightthickness=1)
        panel.pack(fill=tk.X, padx=12, pady=4)

        # ── Row 1: sliders & dropdowns ────────────────────────
        row1 = tk.Frame(panel, bg=THEME["bg_panel"])
        row1.pack(fill=tk.X, padx=14, pady=(10, 4))

        # Tempo / Note Duration slider
        tk.Label(row1, text="Note Duration",
                 font=FONT_LABEL, bg=THEME["bg_panel"], fg=THEME["fg_muted"]
                 ).grid(row=0, column=0, sticky="w")

        self.tempo_var = tk.IntVar(value=200)   # milliseconds per note
        tempo_slider = tk.Scale(
            row1,
            from_=50, to=600,
            orient=tk.HORIZONTAL,
            variable=self.tempo_var,
            label="ms / note",
            length=200,
            bg=THEME["slider_bg"],
            fg=THEME["fg"],
            troughcolor=THEME["bg"],
            highlightthickness=0,
            activebackground=THEME["accent"],
            font=FONT_LABEL,
        )
        tempo_slider.grid(row=0, column=1, sticky="w", padx=(8, 24))

        # Octave base selector
        tk.Label(row1, text="Base Octave",
                 font=FONT_LABEL, bg=THEME["bg_panel"], fg=THEME["fg_muted"]
                 ).grid(row=0, column=2, sticky="w")

        self.octave_var = tk.StringVar(value="3")
        octave_opts = ["1", "2", "3", "4", "5"]
        octave_menu = ttk.Combobox(
            row1,
            textvariable=self.octave_var,
            values=octave_opts,
            state="readonly",
            width=5,
            font=FONT_UI,
        )
        octave_menu.grid(row=0, column=3, sticky="w", padx=(8, 24))

        # Octave range selector
        tk.Label(row1, text="Octave Range",
                 font=FONT_LABEL, bg=THEME["bg_panel"], fg=THEME["fg_muted"]
                 ).grid(row=0, column=4, sticky="w")

        self.octave_range_var = tk.StringVar(value="3")
        range_opts = ["1", "2", "3", "4"]
        range_menu = ttk.Combobox(
            row1,
            textvariable=self.octave_range_var,
            values=range_opts,
            state="readonly",
            width=5,
            font=FONT_UI,
        )
        range_menu.grid(row=0, column=5, sticky="w", padx=8)

        # Style all comboboxes (ttk)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox",
                         fieldbackground=THEME["bg_text"],
                         background=THEME["bg_panel"],
                         foreground=THEME["fg"],
                         selectbackground=THEME["accent2"],
                         bordercolor=THEME["border"])

        # ── Row 2: waveform + action buttons ─────────────────
        row2 = tk.Frame(panel, bg=THEME["bg_panel"])
        row2.pack(fill=tk.X, padx=14, pady=(4, 12))

        # Waveform selector
        tk.Label(row2, text="Waveform",
                 font=FONT_LABEL, bg=THEME["bg_panel"], fg=THEME["fg_muted"]
                 ).pack(side=tk.LEFT)

        self.waveform_var = tk.StringVar(value="sine")
        waveform_opts = ["sine", "triangle", "sawtooth"]
        waveform_menu = ttk.Combobox(
            row2,
            textvariable=self.waveform_var,
            values=waveform_opts,
            state="readonly",
            width=10,
            font=FONT_UI,
        )
        waveform_menu.pack(side=tk.LEFT, padx=(8, 32))

        # Stop button
        self.stop_btn = tk.Button(
            row2,
            text="■  Stop",
            font=FONT_BTN,
            bg=THEME["accent2"],
            fg=THEME["fg"],
            activebackground=THEME["btn_active"],
            activeforeground=THEME["fg"],
            relief=tk.FLAT,
            padx=18,
            pady=6,
            cursor="hand2",
            command=self._on_stop,
        )
        self.stop_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # Convert / Play button  (primary CTA)
        self.play_btn = tk.Button(
            row2,
            text="▶  Convert to Song",
            font=FONT_BTN,
            bg=THEME["accent"],
            fg="#FFFFFF",
            activebackground=THEME["btn_active"],
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=22,
            pady=6,
            cursor="hand2",
            command=self._on_play,
        )
        self.play_btn.pack(side=tk.RIGHT, padx=(0, 8))

    def _build_status_bar(self):
        """Bottom status bar showing note stream and messages."""
        bar = tk.Frame(self, bg=THEME["note_bg"],
                       highlightbackground=THEME["border"], highlightthickness=1)
        bar.pack(fill=tk.X, padx=12, pady=(2, 10))

        self.status_label = tk.Label(
            bar,
            text="Ready — type something and press ▶ Convert to Song",
            font=FONT_NOTE,
            bg=THEME["note_bg"],
            fg=THEME["fg_muted"],
            anchor="w",
            padx=12,
            pady=5,
        )
        self.status_label.pack(fill=tk.X)

        # Note stream ticker (scrolling display of notes being played)
        self.note_label = tk.Label(
            bar,
            text="",
            font=FONT_NOTE,
            bg=THEME["note_bg"],
            fg=THEME["accent"],
            anchor="w",
            padx=12,
            pady=2,
        )
        self.note_label.pack(fill=tk.X)

    # ── Event handlers ───────────────────────────────────────

    def _on_play(self):
        """Parse text, build note sequence, play asynchronously."""
        if self._is_playing:
            self._set_status("Already playing — press ■ Stop first.", "warn")
            return

        text = self.text_area.get("1.0", tk.END).strip()
        if not text or text in ("Start typing here…",):
            self._set_status("Please type something first!", "warn")
            return

        # Read configuration
        duration_ms   = self.tempo_var.get()
        base_octave   = int(self.octave_var.get())
        octave_range  = max(1, int(self.octave_range_var.get()))
        waveform      = self.waveform_var.get()

        # Map every character to a note (or rest)
        notes = []
        note_names_display = []
        for ch in text:
            if ch == "\n":
                # Newlines = short rest (quarter duration)
                notes.append(None)
                note_names_display.append("↵")
                continue
            note = char_to_note(ch, base_octave, octave_range)
            notes.append(note)
            note_names_display.append(note["name"] + str(note["octave"]) if note else "·")

        if not notes:
            self._set_status("No playable notes found.", "warn")
            return

        # Build a readable ticker string (max 80 chars shown)
        ticker = "  ".join(note_names_display)
        if len(ticker) > 80:
            ticker = ticker[:77] + "…"
        self.note_label.config(text=f"♩ {ticker}")

        total_sec = len(notes) * duration_ms / 1000
        self._set_status(
            f"Playing {len(notes)} notes at {duration_ms} ms/note  "
            f"({total_sec:.1f} s total)…",
            "info"
        )
        self._set_playing(True)

        # Play on a background thread — never blocks Tkinter
        self._engine.play_sequence(
            notes,
            duration_ms,
            waveform=waveform,
            on_done=self._on_playback_done,
            on_error=self._on_playback_error,
        )

    def _on_stop(self):
        """Halt playback immediately."""
        self._engine.stop()
        self._set_playing(False)
        self._set_status("Stopped.", "info")
        self.note_label.config(text="")

    def _on_playback_done(self):
        """Called from the worker thread when playback finishes naturally."""
        # Schedule UI update on the main thread via `after`
        self.after(0, lambda: self._set_playing(False))
        self.after(0, lambda: self._set_status("Playback complete. ♪", "ok"))

    def _on_playback_error(self, msg: str):
        """Called from the worker thread on error."""
        self.after(0, lambda: self._set_playing(False))
        self.after(0, lambda: messagebox.showerror("Audio Error", msg))
        self.after(0, lambda: self._set_status(f"Error: {msg}", "warn"))

    # ── UI helpers ────────────────────────────────────────────

    def _set_playing(self, playing: bool):
        self._is_playing = playing
        state = "disabled" if playing else "normal"
        self.play_btn.config(state=state)
        self.play_btn.config(
            bg="#555577" if playing else THEME["accent"]
        )

    def _set_status(self, msg: str, level: str = "info"):
        colours = {
            "info": THEME["fg_muted"],
            "ok":   "#55CC88",
            "warn": "#FFAA44",
        }
        self.status_label.config(text=msg, fg=colours.get(level, THEME["fg_muted"]))

    def _check_audio_backend(self):
        """Warn the user at startup if no audio backend is available."""
        if AUDIO_BACKEND is None:
            self.after(300, lambda: messagebox.showwarning(
                "No Audio Backend",
                "No supported audio backend was found.\n\n"
                "On macOS / Linux, please install simpleaudio:\n"
                "    pip install simpleaudio\n\n"
                "On Windows, winsound should be built-in.\n\n"
                "The app will run but produce no sound."
            ))
            self._set_status("⚠ No audio backend — install simpleaudio.", "warn")
        else:
            self._set_status(
                f"Ready — audio via {AUDIO_BACKEND}. Type something and press ▶!",
                "info"
            )


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = Notepad()
    app.mainloop()
