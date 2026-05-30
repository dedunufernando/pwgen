"""
Mr.Pass GUI — dark / light theme · seed words · history · hints · position rules.

Layout
------
  Title bar ──────────────────────────────── [☀/☾ theme toggle]
  ── separator ────────────────────────────────────────────────
  Constraints  (length · charset · no-consec · entropy · pos rules)
  ┌─ Seed Words (wordlist mode) ──┬─ Constraint Hints ─────────┐
  │ names · dates · keywords       │ plain-language rules        │
  └───────────────────────────────┴────────────────────────────┘
  Output       (file · format · mutations · preset · limit)
  ── action bar: [▶ Generate]  [■ Stop]  live-stats ──────────
  Progress bar
  ┌── Notebook ──────────────────────────────────────────────┐
  │  Tab "Log"      │  Tab "History"                         │
  └──────────────────────────────────────────────────────────┘

Modes
-----
  Combinatorial : No seed words → every combination from charset/length.
  Wordlist      : Seed words provided → mutations of each seed word.
"""
from __future__ import annotations
import datetime
import os
import pathlib
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .hint_parser import parse_hints
from .rule_compiler import compile_rules, RuleConflictError, CHARSETS
from .pipeline import run_pipeline

# ── Default output directory ──────────────────────────────────────────────────
_OUTPUT_DIR = pathlib.Path.home() / "Documents" / "MrPass"

def _ensure_output_dir() -> None:
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

_ensure_output_dir()

# ── UI constants ──────────────────────────────────────────────────────────────

_MUTATIONS = ["none", "standard", "aggressive"]
_PRESETS   = ["(none)", "numeric_7", "policy_enterprise", "ctf_binary"]
_FORMATS   = ["txt", "csv", "json", "gz"]
_FONT      = ("Consolas", 10)
_FONT_H    = ("Consolas", 11, "bold")
_FONT_SM   = ("Consolas", 8)

# ── Catppuccin Mocha (dark) ───────────────────────────────────────────────────
DARK: dict[str, str] = {
    "bg":       "#1e1e2e",
    "fg":       "#cdd6f4",
    "accent":   "#89b4fa",
    "btn_run":  "#a6e3a1",
    "btn_stop": "#f38ba8",
    "btn_fg":   "#1e1e2e",
    "entry_bg": "#313244",
    "log_bg":   "#11111b",
    "muted":    "#6c7086",
    "warn":     "#f9e2af",
    "sel_bg":   "#45475a",
    "seed_bg":  "#1e2d40",   # subtle teal tint for seed words
    "hint_bg":  "#2a1e2e",   # subtle purple tint for hints
}

# ── Violet Bloom (light) — vibrant soft purple palette ───────────────────────
LIGHT: dict[str, str] = {
    "bg":       "#f5f0ff",   # lavender-50 — warm violet background
    "fg":       "#2d1b6e",   # deep violet text — rich contrast
    "accent":   "#7c3aed",   # violet-600 — vivid accent
    "btn_run":  "#059669",   # emerald-600
    "btn_stop": "#dc2626",   # red-600
    "btn_fg":   "#ffffff",
    "entry_bg": "#ffffff",   # pure white inputs pop off tinted bg
    "log_bg":   "#ede9fe",   # violet-100 log panel
    "muted":    "#8b5cf6",   # violet-500 for secondary text
    "warn":     "#d97706",   # amber-600
    "sel_bg":   "#ddd6fe",   # violet-200 selection
    "seed_bg":  "#ecfdf5",   # emerald-50 — refreshing green for seeds
    "hint_bg":  "#fff7ed",   # orange-50 — warm for hints
}

# ── Placeholder helper ────────────────────────────────────────────────────────

class _Placeholder:
    """Gray placeholder text that disappears on focus and returns when empty."""

    def __init__(self, widget: tk.Text, text: str, muted_color: str) -> None:
        self._w        = widget
        self._text     = text
        self._muted    = muted_color
        self._active   = False
        self._real_fg  = widget.cget("fg")
        self._show()
        widget.bind("<FocusIn>",  self._on_focus_in)
        widget.bind("<FocusOut>", self._on_focus_out)

    def _show(self) -> None:
        self._w.configure(state="normal")
        self._w.delete("1.0", "end")
        self._w.insert("1.0", self._text)
        self._w.configure(fg=self._muted)
        self._active = True

    def _on_focus_in(self, _=None) -> None:
        if self._active:
            self._w.delete("1.0", "end")
            self._w.configure(fg=self._real_fg)
            self._active = False

    def _on_focus_out(self, _=None) -> None:
        if not self._w.get("1.0", "end").strip():
            self._show()

    def get_real(self) -> str:
        """Return real content (empty string when placeholder is showing)."""
        if self._active:
            return ""
        return self._w.get("1.0", "end").strip()

    def update_colors(self, fg: str, muted: str) -> None:
        """Called after theme switch."""
        self._real_fg = fg
        self._muted   = muted
        if self._active:
            self._w.configure(fg=muted)
        else:
            self._w.configure(fg=fg)


# ── Tooltip ───────────────────────────────────────────────────────────────────

class _Tip:
    """Hover tooltip attached to any widget."""
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._w = widget; self._text = text; self._top: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show); widget.bind("<Leave>", self._hide)

    def _show(self, _=None) -> None:
        if self._top: return
        x = self._w.winfo_rootx() + 12
        y = self._w.winfo_rooty() + self._w.winfo_height() + 4
        self._top = top = tk.Toplevel(self._w)
        top.wm_overrideredirect(True); top.wm_geometry(f"+{x}+{y}")
        tk.Label(top, text=self._text, justify="left",
                 bg="#313244", fg="#cdd6f4", font=("Consolas", 9),
                 relief="flat", padx=8, pady=5, wraplength=320).pack()

    def _hide(self, _=None) -> None:
        if self._top: self._top.destroy(); self._top = None


# ── MrPassGUI ─────────────────────────────────────────────────────────────────

class PwgenGUI:

    # ── init ──────────────────────────────────────────────────────────────────

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Mr.Pass — Password List Generator")
        self.root.resizable(True, True)
        self.root.minsize(800, 760)

        self._stop_flag = threading.Event()
        self._log_q: queue.Queue = queue.Queue()
        self._running   = False
        self._dark_mode = True
        self.t          = DARK.copy()
        self._last_cfg: dict = {}
        self._seed_tmp: str | None = None   # path to temp seed file

        # History
        self._history:  list[dict]       = []
        self._hist_cfg: dict[str, dict]  = {}   # treeview iid -> cfg

        # Theme registries (populated during _build_ui)
        self._rframes:  list[tk.Frame]       = []
        self._rlabels:  list[tuple]          = []   # (widget, fg_key)
        self._rentries: list[tk.Entry]       = []
        self._rchecks:  list[tk.Checkbutton] = []
        self._rlframes: list[tuple]          = []   # (LabelFrame, bg_key)
        self._rbtns_n:  list[tk.Button]      = []   # neutral buttons
        self._rtexts:   list[tuple]          = []   # (Text, bg_key)

        # Placeholder helpers
        self._seed_ph: _Placeholder | None = None
        self._hint_ph: _Placeholder | None = None

        self._build_ui()
        self._apply_ttk_style()
        self._poll_log()

    # ── Theme registry shortcuts ──────────────────────────────────────────────

    def _rl(self, w: tk.Label, fg: str = "fg") -> tk.Label:
        self._rlabels.append((w, fg)); return w

    def _rf(self, w: tk.Frame, bg: str = "bg") -> tk.Frame:
        self._rframes.append(w); return w

    def _re(self, w: tk.Entry) -> tk.Entry:
        self._rentries.append(w); return w

    def _rc(self, w: tk.Checkbutton) -> tk.Checkbutton:
        self._rchecks.append(w); return w

    def _rlf(self, w: tk.LabelFrame, bg: str = "bg") -> tk.LabelFrame:
        self._rlframes.append((w, bg)); return w

    def _rbn(self, w: tk.Button) -> tk.Button:
        self._rbtns_n.append(w); return w

    def _rt(self, w: tk.Text, bg: str = "entry_bg") -> tk.Text:
        self._rtexts.append((w, bg)); return w

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = self.t

        # ── Title bar ──
        top = self._rf(tk.Frame(self.root, bg=t["bg"]))
        top.pack(fill="x", padx=10, pady=(8, 2))

        self._rl(tk.Label(top,
            text="Mr.Pass  •  Password Candidate Generator",
            font=("Consolas", 14, "bold"), bg=t["bg"], fg=t["accent"],
        ), "accent").pack(side="left")

        self.theme_btn = self._rbn(tk.Button(
            top, text="☀  Light mode", command=self._toggle_theme,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT,
            relief="flat", padx=10, pady=3,
            activebackground=t["accent"], activeforeground=t["bg"],
            cursor="hand2",
        ))
        self.theme_btn.pack(side="right")

        self._rl(tk.Label(self.root,
            text="⚠  FOR AUTHORIZED SECURITY TESTING, CTF & PENTESTING ONLY",
            font=("Consolas", 9, "bold"), bg=t["bg"], fg=t["btn_stop"],
        ), "btn_stop").pack(fill="x", padx=10)

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", pady=6)

        # ── Constraints frame ──
        cf = self._rlf(tk.LabelFrame(self.root,
            text=" Constraints ", font=_FONT_H,
            bg=t["bg"], fg=t["accent"], bd=1, relief="groove",
        ))
        cf.pack(fill="x", padx=10, pady=(0, 4))

        # Row 0 — Length / Min / Max
        self._add_row(cf, 0, [
            ("Length (exact)", "length_var", "",   "Exact length overrides Min/Max. Leave blank to use range."),
            ("Min length",     "min_var",    "1",  "Minimum password length (inclusive)."),
            ("Max length",     "max_var",    "16", "Maximum password length (inclusive)."),
        ])

        # Row 1 — Character types
        self._rl(tk.Label(cf, text="Include chars", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=1, column=0, sticky="e", padx=(8, 4), pady=4)

        char_frame = self._rf(tk.Frame(cf, bg=t["bg"]))
        char_frame.grid(row=1, column=1, columnspan=7, sticky="w", pady=4)

        self.inc_digits_var  = tk.BooleanVar(value=True)
        self.inc_lower_var   = tk.BooleanVar(value=False)
        self.inc_upper_var   = tk.BooleanVar(value=False)
        self.inc_symbols_var = tk.BooleanVar(value=False)

        for text, var, tip in [
            ("0–9  Digits",    self.inc_digits_var,
             "Include numbers 0-9.\nGood for PIN-style or numeric passwords."),
            ("a–z  Lowercase", self.inc_lower_var,
             "Include lowercase English letters a-z."),
            ("A–Z  Uppercase", self.inc_upper_var,
             "Include uppercase English letters A-Z."),
            ("!@#  Symbols",   self.inc_symbols_var,
             "Include common symbols: !@#$%^&*()_+-=[]{}|;':\",./<>?\nGreat for stronger passwords."),
        ]:
            cb = self._rc(tk.Checkbutton(char_frame, text=text, variable=var,
                bg=t["bg"], fg=t["fg"], selectcolor=t["entry_bg"],
                activebackground=t["bg"], font=_FONT, cursor="hand2",
            ))
            cb.pack(side="left", padx=(0, 14))
            _Tip(cb, tip)

        self._rl(tk.Label(char_frame, text="Extra:", bg=t["bg"], fg=t["muted"], font=_FONT_SM),
             "muted").pack(side="left", padx=(4, 2))
        self.extra_chars_var = tk.StringVar()
        ec_e = self._re(tk.Entry(char_frame, textvariable=self.extra_chars_var, width=10,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        ec_e.pack(side="left")
        _Tip(ec_e, "Add any extra characters not covered by the checkboxes.\ne.g. type  +-=  to also allow those.")

        # Row 2 — No-consec / Max repeats
        self._rl(tk.Label(cf, text="No-consec (char:n)", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=2, column=0, sticky="e", padx=(8, 2), pady=3)
        self.no_consec_var = tk.StringVar()
        nc_e = self._re(tk.Entry(cf, textvariable=self.no_consec_var, width=10,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        nc_e.grid(row=2, column=1, padx=4, pady=3)
        _Tip(nc_e, 'No more than N of a character in a row.\ne.g. "0:3" = max 3 zeros  |  "any:3" = any char')

        self._rl(tk.Label(cf, text="Max repeats", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=2, column=2, sticky="e", padx=(12, 2), pady=3)
        self.max_repeats_var = tk.StringVar()
        mr_e = self._re(tk.Entry(cf, textvariable=self.max_repeats_var, width=6,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        mr_e.grid(row=2, column=3, padx=4, pady=3)
        _Tip(mr_e, "Max times any single digit may appear in the whole password.")

        # Row 3 — Entropy / Walks / hint pointer
        self._rl(tk.Label(cf, text="Min entropy (bits)", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=3, column=0, sticky="e", padx=(8, 2), pady=3)
        self.entropy_var = tk.StringVar()
        ent_e = self._re(tk.Entry(cf, textvariable=self.entropy_var, width=8,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        ent_e.grid(row=3, column=1, padx=4, pady=3)
        _Tip(ent_e, "Shannon entropy in bits.\n30+ moderate  |  50+ strong  |  70+ very strong")

        self.no_walks_var = tk.BooleanVar()
        self._rc(tk.Checkbutton(cf,
            text="No keyboard walks", variable=self.no_walks_var,
            bg=t["bg"], fg=t["fg"], selectcolor=t["entry_bg"],
            activebackground=t["bg"], font=_FONT,
        )).grid(row=3, column=2, columnspan=2, sticky="w", padx=12, pady=3)

        self._rl(tk.Label(cf,
            text='💡 Use Constraint Hints below  →  "must have digit"  "must have upper"',
            bg=t["bg"], fg=t["muted"], font=_FONT_SM,
        ), "muted").grid(row=3, column=4, columnspan=5, sticky="w", padx=8, pady=3)

        # Row 4 — Position rules
        self._rl(tk.Label(cf, text="Must not start with", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=4, column=0, sticky="e", padx=(8, 2), pady=3)
        self.must_not_start_var = tk.StringVar()
        mns_e = self._re(tk.Entry(cf, textvariable=self.must_not_start_var, width=14,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        mns_e.grid(row=4, column=1, padx=4, pady=3)
        _Tip(mns_e, "Comma-separated.\ne.g. '0,1' rejects passwords starting with 0 or 1.")

        self._rl(tk.Label(cf, text="Must not end with", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=4, column=2, sticky="e", padx=(12, 2), pady=3)
        self.must_not_end_var = tk.StringVar()
        mne_e = self._re(tk.Entry(cf, textvariable=self.must_not_end_var, width=14,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        mne_e.grid(row=4, column=3, padx=4, pady=3)
        _Tip(mne_e, "Comma-separated.\ne.g. '000,111' rejects passwords ending with those.")

        self._rl(tk.Label(cf, text="Must start with", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=4, column=4, sticky="e", padx=(12, 2), pady=3)
        self.must_start_with_var = tk.StringVar()
        msw_e = self._re(tk.Entry(cf, textvariable=self.must_start_with_var, width=14,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        msw_e.grid(row=4, column=5, padx=4, pady=3)
        _Tip(msw_e, "Comma-separated prefixes.\ne.g. 'admin,root' keeps only passwords starting with those.")

        self._rl(tk.Label(cf,
            text="(comma-separated for position fields)",
            bg=t["bg"], fg=t["muted"], font=_FONT_SM,
        ), "muted").grid(row=4, column=6, columnspan=3, sticky="w", padx=4)

        # ── Seed Words + Constraint Hints (side-by-side) ──────────────────────
        sw_row = self._rf(tk.Frame(self.root, bg=t["bg"]))
        sw_row.pack(fill="x", padx=10, pady=4)

        # LEFT — Seed Words (wordlist mode)
        sf = self._rlf(tk.LabelFrame(sw_row,
            text=" 🌱 Seed Words  (wordlist mode) ", font=_FONT_H,
            bg=t["seed_bg"], fg=t["accent"], bd=1, relief="groove",
        ), "seed_bg")
        sf.pack(side="left", fill="both", expand=True, padx=(0, 4))

        seed_hdr = tk.Frame(sf, bg=t["seed_bg"])
        seed_hdr.pack(fill="x", padx=6, pady=(4, 0))
        self._rframes.append(seed_hdr)
        self._rl(tk.Label(seed_hdr,
            text="Type base words (names, dates, keywords) — one per line.\n"
                 "Leave blank for combinatorial mode (all combinations).",
            bg=t["seed_bg"], fg=t["fg"], font=_FONT_SM,
            justify="left",
        ), "fg").pack(side="left")
        self._rbn(tk.Button(seed_hdr, text="Clear", command=self._clear_seeds,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT_SM,
            relief="flat", padx=6, pady=2, cursor="hand2",
            activebackground=t["accent"], activeforeground=t["bg"],
        )).pack(side="right")

        self.seed_text = scrolledtext.ScrolledText(sf,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"],
            font=_FONT, height=4, wrap="word",
        )
        self.seed_text.pack(fill="both", expand=True, padx=6, pady=(2, 2))
        self._rt(self.seed_text, "entry_bg")

        self._seed_ph = _Placeholder(
            self.seed_text,
            "e.g.\nJohn\ndedunu\n2004\nwattala",
            t["muted"],
        )

        self._rl(tk.Label(sf,
            text="💡 Set Mutations → 'standard' or 'aggressive' for more variants",
            bg=t["seed_bg"], fg=t["muted"], font=_FONT_SM,
        ), "muted").pack(anchor="w", padx=6, pady=(0, 4))

        # RIGHT — Constraint Hints
        hf = self._rlf(tk.LabelFrame(sw_row,
            text=" 💬 Constraint Hints  (plain language) ", font=_FONT_H,
            bg=t["hint_bg"], fg=t["accent"], bd=1, relief="groove",
        ), "hint_bg")
        hf.pack(side="right", fill="both", expand=True, padx=(4, 0))

        hint_hdr = tk.Frame(hf, bg=t["hint_bg"])
        hint_hdr.pack(fill="x", padx=6, pady=(4, 0))
        self._rframes.append(hint_hdr)
        self._rl(tk.Label(hint_hdr,
            text='One rule per line in plain English.\nThe tool understands natural language constraints.',
            bg=t["hint_bg"], fg=t["fg"], font=_FONT_SM,
            justify="left",
        ), "fg").pack(side="left")
        self._rbn(tk.Button(hint_hdr, text="Clear", command=self._clear_hints,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT_SM,
            relief="flat", padx=6, pady=2, cursor="hand2",
            activebackground=t["accent"], activeforeground=t["bg"],
        )).pack(side="right")

        self.hints_text = scrolledtext.ScrolledText(hf,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"],
            font=_FONT, height=4, wrap="word",
        )
        self.hints_text.pack(fill="both", expand=True, padx=6, pady=(2, 2))
        self._rt(self.hints_text, "entry_bg")

        self._hint_ph = _Placeholder(
            self.hints_text,
            "e.g.\n7 characters\nmust have digit\nno 3 zeros in a row\nno keyboard walk",
            t["muted"],
        )

        self._rl(tk.Label(hf,
            text='Also: "starts with admin"  •  "25 bits entropy"  •  "digits only"',
            bg=t["hint_bg"], fg=t["muted"], font=_FONT_SM,
        ), "muted").pack(anchor="w", padx=6, pady=(0, 4))

        # ── Output frame ──────────────────────────────────────────────────────
        of = self._rlf(tk.LabelFrame(self.root,
            text=" Output ", font=_FONT_H,
            bg=t["bg"], fg=t["accent"], bd=1, relief="groove",
        ))
        of.pack(fill="x", padx=10, pady=4)

        self._rl(tk.Label(of, text="File", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=0, column=0, sticky="e", padx=(8, 2), pady=4)

        # Default output path inside MrPass folder
        default_out = str(_OUTPUT_DIR / "wordlist.txt")
        self.output_var = tk.StringVar(value=default_out)
        self._re(tk.Entry(of, textvariable=self.output_var, width=34,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT)
             ).grid(row=0, column=1, padx=4, pady=4)

        self._rbn(tk.Button(of, text="Browse…", command=self._browse_output,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT,
            activebackground=t["accent"], activeforeground=t["bg"],
        )).grid(row=0, column=2, padx=(4, 2), pady=4)

        self._rbn(tk.Button(of, text="📂 Open Folder", command=self._open_output_folder,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT,
            activebackground=t["accent"], activeforeground=t["bg"],
            cursor="hand2",
        )).grid(row=0, column=3, padx=(2, 8), pady=4)

        self._rl(tk.Label(of, text="Format", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=0, column=4, sticky="e", padx=(4, 2))
        self.fmt_var = tk.StringVar(value="txt")
        ttk.Combobox(of, textvariable=self.fmt_var, values=_FORMATS,
                     state="readonly", width=6, font=_FONT,
                    ).grid(row=0, column=5, padx=4, pady=4)

        self._rl(tk.Label(of, text="Mutations", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=0, column=6, sticky="e", padx=(8, 2))
        self.mutations_var = tk.StringVar(value="none")
        ttk.Combobox(of, textvariable=self.mutations_var, values=_MUTATIONS,
                     state="readonly", width=12, font=_FONT,
                    ).grid(row=0, column=7, padx=4, pady=4)

        self._rl(tk.Label(of, text="Preset", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=1, column=0, sticky="e", padx=(8, 2), pady=4)
        self.preset_var = tk.StringVar(value="(none)")
        ttk.Combobox(of, textvariable=self.preset_var, values=_PRESETS,
                     state="readonly", width=18, font=_FONT,
                    ).grid(row=1, column=1, padx=4, pady=4)
        self._rbn(tk.Button(of, text="Load preset", command=self._load_preset,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT,
            activebackground=t["accent"], activeforeground=t["bg"],
        )).grid(row=1, column=2, columnspan=2, padx=4, pady=4)

        self._rl(tk.Label(of, text="Limit candidates", bg=t["bg"], fg=t["fg"], font=_FONT)
             ).grid(row=1, column=4, sticky="e", padx=(4, 2))
        self.limit_var = tk.StringVar()
        lim_e = self._re(tk.Entry(of, textvariable=self.limit_var, width=12,
            bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT))
        lim_e.grid(row=1, column=5, padx=4, pady=4)
        _Tip(lim_e, "Stop after this many candidates. Leave blank for unlimited.")

        # Mode indicator
        self.mode_label = self._rl(tk.Label(of,
            text="Mode: combinatorial", bg=t["bg"], fg=t["muted"], font=_FONT_SM,
        ), "muted")
        self.mode_label.grid(row=1, column=6, columnspan=2, sticky="w", padx=12)
        self.seed_text.bind("<<Modified>>", self._update_mode_label)
        self.seed_text.bind("<KeyRelease>", self._update_mode_label)

        # ── Action buttons ────────────────────────────────────────────────────
        bf = self._rf(tk.Frame(self.root, bg=t["bg"]))
        bf.pack(fill="x", padx=10, pady=6)

        self.run_btn = tk.Button(bf,
            text="▶  Generate", command=self._start_generation,
            bg=t["btn_run"], fg=t["btn_fg"], activebackground=t["btn_run"],
            font=("Consolas", 11, "bold"), width=16,
        )
        self.run_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = tk.Button(bf,
            text="■  Stop", command=self._stop_generation,
            bg=t["btn_stop"], fg=t["btn_fg"], activebackground=t["btn_stop"],
            font=("Consolas", 11, "bold"), width=10, state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(0, 20))

        self.stats_label = self._rl(
            tk.Label(bf, text="", bg=t["bg"], fg=t["accent"], font=_FONT), "accent"
        )
        self.stats_label.pack(side="left", fill="x", expand=True)

        # ── Progress bar ──────────────────────────────────────────────────────
        pf = self._rf(tk.Frame(self.root, bg=t["bg"]))
        pf.pack(fill="x", padx=10, pady=2)
        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(pf, variable=self.progress_var, mode="indeterminate")
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.count_label = self._rl(
            tk.Label(pf, text="Ready — fill in constraints and click Generate", bg=t["bg"], fg=t["fg"], font=_FONT, width=40)
        )
        self.count_label.pack(side="left")

        # ── Notebook: Log | History ───────────────────────────────────────────
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(4, 8))

        log_tab = self._rf(tk.Frame(self.nb, bg=t["bg"]))
        self.nb.add(log_tab, text="  Log  ")
        self.log_box = scrolledtext.ScrolledText(log_tab,
            bg=t["log_bg"], fg=t["fg"], font=_FONT,
            state="disabled", wrap="word", height=8,
        )
        self.log_box.pack(fill="both", expand=True, padx=4, pady=4)
        self._rt(self.log_box, "log_bg")
        self._refresh_log_tags()

        # Write a welcome message to the log
        self.log_box.configure(state="normal")
        self.log_box.insert("end", "[INFO] Mr.Pass ready.\n", "INFO")
        self.log_box.insert("end", f"[INFO] Output folder: {_OUTPUT_DIR}\n", "INFO")
        self.log_box.insert("end", "[INFO] Fill in constraints above and click ▶ Generate.\n", "INFO")
        self.log_box.configure(state="disabled")

        hist_tab = self._rf(tk.Frame(self.nb, bg=t["bg"]))
        self.nb.add(hist_tab, text="  History  ")
        self._build_history_tab(hist_tab)

    # ── History tab ───────────────────────────────────────────────────────────

    def _build_history_tab(self, parent: tk.Frame) -> None:
        t = self.t
        self._rl(tk.Label(parent,
            text="Previously generated password lists — select a row to reuse its settings.",
            bg=t["bg"], fg=t["muted"], font=_FONT_SM,
        ), "muted").pack(anchor="w", padx=6, pady=(6, 2))

        tree_frame = self._rf(tk.Frame(parent, bg=t["bg"]))
        tree_frame.pack(fill="both", expand=True, padx=6, pady=2)

        cols = ("ts", "candidates", "file", "charset", "length", "mode")
        self.hist_tree = ttk.Treeview(tree_frame, columns=cols,
                                      show="headings", selectmode="browse", height=7)
        for col, heading, width, anchor in [
            ("ts",         "Timestamp",   155, "w"),
            ("candidates", "Candidates",  100, "e"),
            ("file",       "Output File", 210, "w"),
            ("charset",    "Charset",      75, "w"),
            ("length",     "Length",       65, "w"),
            ("mode",       "Mode",         90, "w"),
        ]:
            self.hist_tree.heading(col, text=heading)
            self.hist_tree.column(col, width=width, anchor=anchor,
                                  stretch=(col == "file"))

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self.hist_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.hist_tree.xview)
        self.hist_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.hist_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1); tree_frame.columnconfigure(0, weight=1)
        self.hist_tree.bind("<<TreeviewSelect>>", self._on_hist_select)

        self.hist_empty = self._rl(tk.Label(parent,
            text="No history yet — run a generation to record it here.",
            bg=t["bg"], fg=t["muted"], font=_FONT,
        ), "muted")
        self.hist_empty.pack(pady=6)

        btn_row = self._rf(tk.Frame(parent, bg=t["bg"]))
        btn_row.pack(fill="x", padx=6, pady=(2, 8))

        self.hist_reuse_btn = self._rbn(tk.Button(btn_row,
            text="↩  Reuse Settings", command=self._reuse_history,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT,
            relief="flat", padx=10, pady=4, cursor="hand2",
            activebackground=t["accent"], activeforeground=t["bg"],
            state="disabled",
        ))
        self.hist_reuse_btn.pack(side="left", padx=(0, 6))
        _Tip(self.hist_reuse_btn, "Restore all settings from the selected run into the form.")

        self.hist_open_btn = self._rbn(tk.Button(btn_row,
            text="📂 Open File", command=self._open_hist_file,
            bg=t["entry_bg"], fg=t["fg"], font=_FONT,
            relief="flat", padx=10, pady=4, cursor="hand2",
            activebackground=t["accent"], activeforeground=t["bg"],
            state="disabled",
        ))
        self.hist_open_btn.pack(side="left", padx=(0, 6))

        self.hist_clear_btn = tk.Button(btn_row,
            text="🗑  Clear History", command=self._clear_history,
            bg=t["entry_bg"], fg=t["btn_stop"], font=_FONT,
            relief="flat", padx=10, pady=4, cursor="hand2",
            activebackground=t["btn_stop"], activeforeground=t["btn_fg"],
        )
        self.hist_clear_btn.pack(side="right")

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _toggle_theme(self) -> None:
        self._dark_mode = not self._dark_mode
        self.t = DARK.copy() if self._dark_mode else LIGHT.copy()
        self._apply_theme()
        self._apply_ttk_style()
        self.theme_btn.configure(
            text="☀  Light mode" if self._dark_mode else "☾  Dark mode"
        )

    def _apply_theme(self) -> None:
        t = self.t
        self.root.configure(bg=t["bg"])

        for w in self._rframes:
            try: w.configure(bg=t["bg"])
            except tk.TclError: pass

        for w, fg_key in self._rlabels:
            try:
                parent_bg = t.get(_bg_key_for(w), t["bg"])
                w.configure(bg=parent_bg, fg=t[fg_key])
            except tk.TclError: pass

        for w in self._rentries:
            try: w.configure(bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"])
            except tk.TclError: pass

        for w in self._rchecks:
            try: w.configure(bg=t["bg"], fg=t["fg"],
                              selectcolor=t["entry_bg"], activebackground=t["bg"])
            except tk.TclError: pass

        for w, bg_key in self._rlframes:
            try: w.configure(bg=t[bg_key], fg=t["accent"])
            except tk.TclError: pass

        for w in self._rbtns_n:
            try: w.configure(bg=t["entry_bg"], fg=t["fg"],
                              activebackground=t["accent"], activeforeground=t["bg"])
            except tk.TclError: pass

        for w, bg_key in self._rtexts:
            try: w.configure(bg=t[bg_key], fg=t["fg"], insertbackground=t["fg"])
            except tk.TclError: pass

        # Fix labels inside LabelFrames (seed_bg / hint_bg panels)
        for w, fg_key in self._rlabels:
            try:
                if isinstance(w.master, tk.Frame):
                    parent_lf = w.master.master
                    if isinstance(parent_lf, tk.LabelFrame):
                        for (lf, bg_key_lf) in self._rlframes:
                            if lf is parent_lf:
                                w.configure(bg=t[bg_key_lf], fg=t[fg_key])
                                break
            except (tk.TclError, AttributeError): pass

        # Fix frames that sit inside LabelFrames (seed_hdr / hint_hdr)
        for w in self._rframes:
            try:
                if isinstance(w.master, tk.LabelFrame):
                    for (lf, bg_key_lf) in self._rlframes:
                        if lf is w.master:
                            w.configure(bg=t[bg_key_lf])
                            break
            except (tk.TclError, AttributeError): pass

        try:
            self.run_btn.configure(bg=t["btn_run"], fg=t["btn_fg"], activebackground=t["btn_run"])
            self.stop_btn.configure(bg=t["btn_stop"], fg=t["btn_fg"], activebackground=t["btn_stop"])
            self.hist_clear_btn.configure(bg=t["entry_bg"], fg=t["btn_stop"],
                                          activebackground=t["btn_stop"],
                                          activeforeground=t["btn_fg"])
        except (tk.TclError, AttributeError): pass

        # Update placeholder colors
        if self._seed_ph:
            self._seed_ph.update_colors(t["fg"], t["muted"])
        if self._hint_ph:
            self._hint_ph.update_colors(t["fg"], t["muted"])

        self._refresh_log_tags()

    def _refresh_log_tags(self) -> None:
        t = self.t
        try:
            self.log_box.tag_config("INFO",    foreground=t["fg"])
            self.log_box.tag_config("OK",      foreground=t["btn_run"])
            self.log_box.tag_config("WARN",    foreground=t["warn"])
            self.log_box.tag_config("ERROR",   foreground=t["btn_stop"])
            self.log_box.tag_config("HEADING", foreground=t["accent"])
        except (tk.TclError, AttributeError): pass

    def _apply_ttk_style(self) -> None:
        t = self.t
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("TCombobox",
                    fieldbackground=t["entry_bg"], background=t["entry_bg"],
                    foreground=t["fg"], selectbackground=t["sel_bg"],
                    selectforeground=t["fg"])
        s.map("TCombobox",
              fieldbackground=[("readonly", t["entry_bg"])],
              foreground=[("readonly", t["fg"])])
        s.configure("Horizontal.TProgressbar",
                    troughcolor=t["entry_bg"], background=t["accent"], thickness=14)
        s.configure("TSeparator", background=t["muted"])
        s.configure("TNotebook", background=t["bg"], tabmargins=[2, 4, 2, 0])
        s.configure("TNotebook.Tab", background=t["entry_bg"], foreground=t["fg"],
                    padding=[12, 4], font=_FONT)
        s.map("TNotebook.Tab",
              background=[("selected", t["bg"]), ("active", t["sel_bg"])],
              foreground=[("selected", t["accent"])])
        s.configure("Treeview",
                    background=t["entry_bg"], foreground=t["fg"],
                    fieldbackground=t["entry_bg"], rowheight=24, font=_FONT)
        s.configure("Treeview.Heading",
                    background=t["bg"], foreground=t["accent"],
                    font=("Consolas", 10, "bold"))
        s.map("Treeview",
              background=[("selected", t["sel_bg"])],
              foreground=[("selected", t["fg"])])
        s.configure("TScrollbar",
                    background=t["entry_bg"], troughcolor=t["bg"], arrowcolor=t["fg"])

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _add_row(self, parent: tk.Frame, row: int, fields: list) -> None:
        t = self.t
        for col_offset, item in enumerate(fields):
            label, attr, default = item[0], item[1], item[2]
            tip = item[3] if len(item) > 3 else ""
            lbl = tk.Label(parent, text=label, bg=t["bg"], fg=t["fg"], font=_FONT)
            lbl.grid(row=row, column=col_offset*2,
                     sticky="e", padx=(8 if col_offset == 0 else 12, 2), pady=3)
            self._rl(lbl)
            var = tk.StringVar(value=default)
            setattr(self, attr, var)
            ent = tk.Entry(parent, textvariable=var, width=6,
                           bg=t["entry_bg"], fg=t["fg"], insertbackground=t["fg"], font=_FONT)
            ent.grid(row=row, column=col_offset*2+1, padx=4, pady=3)
            self._re(ent)
            if tip: _Tip(ent, tip)

    def _log(self, msg: str, level: str = "INFO") -> None:
        self._log_q.put((level, msg))

    def _poll_log(self) -> None:
        while not self._log_q.empty():
            level, msg = self._log_q.get_nowait()
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{level}] {msg}\n", level)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(100, self._poll_log)

    def _clear_seeds(self) -> None:
        self.seed_text.configure(fg=self.t["fg"])
        self.seed_text.delete("1.0", "end")
        if self._seed_ph:
            self._seed_ph._active = False
        self._update_mode_label()

    def _clear_hints(self) -> None:
        self.hints_text.configure(fg=self.t["fg"])
        self.hints_text.delete("1.0", "end")
        if self._hint_ph:
            self._hint_ph._active = False

    def _update_mode_label(self, _=None) -> None:
        seeds = self._seed_ph.get_real() if self._seed_ph else self.seed_text.get("1.0", "end").strip()
        if seeds:
            words = [w for w in seeds.splitlines() if w.strip()]
            self.mode_label.configure(
                text=f"Mode: wordlist  ({len(words)} seed word{'s' if len(words)!=1 else ''})",
                fg=self.t["accent"],
            )
        else:
            self.mode_label.configure(text="Mode: combinatorial", fg=self.t["muted"])

    def _browse_output(self) -> None:
        initial = str(_OUTPUT_DIR)
        path = filedialog.asksaveasfilename(
            initialdir=initial,
            defaultextension=".txt",
            filetypes=[("Text","*.txt"),("Gzip","*.gz"),
                       ("CSV","*.csv"),("JSON","*.json"),("All","*.*")],
        )
        if path: self.output_var.set(path)

    def _open_output_folder(self) -> None:
        """Open the folder that contains the current output file."""
        out_path = self.output_var.get().strip() or str(_OUTPUT_DIR)
        folder = str(pathlib.Path(out_path).parent)
        # Make sure it exists
        try: pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
        except Exception: pass
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showwarning("Cannot Open Folder", str(exc))

    def _load_preset(self) -> None:
        import json; from pathlib import Path
        preset = self.preset_var.get()
        if preset == "(none)": return
        pp = Path(__file__).parent.parent / "config" / "presets" / f"{preset}.json"
        if not pp.exists():
            self._log(f"Preset not found: {pp}", "WARN"); return
        self._apply_cfg_to_form(json.loads(pp.read_text()))
        self._log(f"Preset '{preset}' loaded.", "OK")

    # ── Validation ────────────────────────────────────────────────────────────

    def _pre_generate_checks(self) -> bool:
        """Run friendly pre-flight checks. Return False to abort, True to proceed."""
        issues: list[str] = []
        tips:   list[str] = []

        # 1. Check at least one char type selected
        if not any([
            self.inc_digits_var.get(),
            self.inc_lower_var.get(),
            self.inc_upper_var.get(),
            self.inc_symbols_var.get(),
            self.extra_chars_var.get().strip(),
        ]):
            issues.append("• No character types selected.\n  Tick at least one box under 'Include chars'.")

        # 2. Check combinatorial mode without length bounds
        seed_raw = self._seed_ph.get_real() if self._seed_ph else self.seed_text.get("1.0", "end").strip()
        if not seed_raw:
            length_set  = self.length_var.get().strip()
            max_set     = self.max_var.get().strip()
            max_val     = int(max_set) if max_set.isdigit() else 16
            if not length_set and max_val > 10:
                tips.append(
                    f"• Combinatorial mode with Max length={max_val} can generate\n"
                    f"  billions of candidates — consider setting Length (exact)\n"
                    f"  or a lower Max length, or using Limit candidates."
                )

        # 3. Wordlist mode tip about length filter
        if seed_raw:
            length_set = self.length_var.get().strip()
            if length_set and length_set.isdigit():
                target_len = int(length_set)
                words = [w.strip() for w in seed_raw.splitlines() if w.strip()]
                mismatched = [w for w in words if len(w) != target_len]
                if mismatched:
                    tips.append(
                        f"• You have an exact Length={target_len} set.\n"
                        f"  {len(mismatched)} of your {len(words)} seed word(s) have\n"
                        f"  different lengths and will be filtered out.\n"
                        f"  Clear the Length field to accept all seeds."
                    )
            if self.mutations_var.get() == "none":
                tips.append(
                    "• Wordlist mode with Mutations='none' only outputs exact seed words.\n"
                    "  Set Mutations → 'standard' or 'aggressive' for more variants."
                )

        if issues:
            messagebox.showerror(
                "Mr.Pass — Fix before generating",
                "Please fix the following before generating:\n\n" + "\n\n".join(issues)
            )
            return False

        if tips:
            answer = messagebox.askokcancel(
                "Mr.Pass — Heads up",
                "A few things to note:\n\n" + "\n\n".join(tips) + "\n\nContinue anyway?"
            )
            return bool(answer)

        return True

    # ── Config ────────────────────────────────────────────────────────────────

    def _build_cfg(self) -> dict:
        cfg: dict = {}

        # Build charset from the Include checkboxes
        _DIGITS  = "0123456789"
        _LOWER   = "abcdefghijklmnopqrstuvwxyz"
        _UPPER   = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        _SYMBOLS = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
        charset = ""
        if self.inc_digits_var.get():  charset += _DIGITS
        if self.inc_lower_var.get():   charset += _LOWER
        if self.inc_upper_var.get():   charset += _UPPER
        if self.inc_symbols_var.get(): charset += _SYMBOLS
        for c in self.extra_chars_var.get():
            if c not in charset:
                charset += c
        if not charset:
            raise ValueError(
                "No character types selected.\n\n"
                "Tick at least one box under 'Include chars'\n"
                "(Digits, Lowercase, Uppercase, or Symbols)."
            )
        cfg["charset"] = "custom"
        cfg["custom_chars"] = charset

        if self.length_var.get().strip():
            cfg["length"] = int(self.length_var.get())
        else:
            if self.min_var.get().strip():  cfg["min_length"] = int(self.min_var.get())
            if self.max_var.get().strip():  cfg["max_length"] = int(self.max_var.get())

        if self.no_consec_var.get().strip():
            parts = self.no_consec_var.get().split(":")
            cfg["no_consecutive"] = [{"char": parts[0] if parts else "any",
                                       "count": int(parts[1]) if len(parts) > 1 else 3}]

        if self.max_repeats_var.get().strip():
            cfg["max_repeats"] = {"digits": int(self.max_repeats_var.get())}

        if self.entropy_var.get().strip():
            cfg["entropy"] = {"min_bits": float(self.entropy_var.get())}

        if self.no_walks_var.get():
            cfg["keyboard_walk"] = {"reject_if_walk_ratio_above": 0.5}

        if self.mutations_var.get() != "none":
            cfg["mutations"] = {"profile": self.mutations_var.get(), "max_expansion": 50}

        pos_rules: dict = {}
        if self.must_not_start_var.get().strip():
            pos_rules["must_not_start_with"] = [
                v.strip() for v in self.must_not_start_var.get().split(",") if v.strip()]
        if self.must_not_end_var.get().strip():
            pos_rules["must_not_end_with"] = [
                v.strip() for v in self.must_not_end_var.get().split(",") if v.strip()]
        if pos_rules: cfg["position_rules"] = pos_rules

        if self.must_start_with_var.get().strip():
            cfg.setdefault("patterns", {})["startswith"] = [
                v.strip() for v in self.must_start_with_var.get().split(",") if v.strip()]

        # Constraint hints — merge; explicit GUI fields take priority
        hints_raw = self._hint_ph.get_real() if self._hint_ph else self.hints_text.get("1.0", "end").strip()
        if hints_raw:
            hint_lines = [ln.strip() for ln in hints_raw.splitlines() if ln.strip()]
            hint_cfg = parse_hints(hint_lines)
            for key, val in hint_cfg.items():
                if key not in cfg:
                    cfg[key] = val
                elif key == "patterns" and isinstance(val, dict):
                    for pk, pv in val.items(): cfg["patterns"].setdefault(pk, pv)
                elif key == "charset_options" and isinstance(val, dict):
                    for ok, ov in val.items():
                        cfg.setdefault("charset_options", {}).setdefault(ok, ov)

        # Seed words — wordlist mode
        seed_raw = self._seed_ph.get_real() if self._seed_ph else self.seed_text.get("1.0", "end").strip()
        if seed_raw:
            words = [w.strip() for w in seed_raw.splitlines() if w.strip()]
            if words:
                # Auto-expand charset so letters/symbols in seeds aren't filtered out
                if cfg.get("charset") == "digits" or (
                    cfg.get("charset") == "custom" and
                    cfg.get("custom_chars", "").isdigit()
                ):
                    cfg["charset"] = "ascii"
                    self._log("Charset auto-expanded to 'ascii' for wordlist mode.", "INFO")
                # Write seeds to temp file
                fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="mrpass_seeds_")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    for w in words:
                        f.write(w + "\n")
                self._seed_tmp = tmp
                cfg["wordlist"] = {"tier": "none", "custom_path": tmp}

        # Output path — ensure parent directory exists
        out_path = self.output_var.get().strip() or str(_OUTPUT_DIR / "wordlist.txt")
        try:
            pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        limit = self.limit_var.get().strip()
        cfg["output"] = {
            "format": self.fmt_var.get(),
            "path":   out_path,
            "include_header": True,
            "sort_by": "none",
            **({"max_candidates": int(limit)} if limit else {}),
        }
        return cfg

    def _apply_cfg_to_form(self, cfg: dict) -> None:
        """Populate all form fields from a cfg dict (preset load + history reuse)."""
        cs = cfg.get("charset", "digits")
        cc = cfg.get("custom_chars", "")
        if cs == "custom" and cc:
            chars = cc
        else:
            chars = CHARSETS.get(cs, "0123456789")
        self.inc_digits_var.set(any(c.isdigit()     for c in chars))
        self.inc_lower_var.set(any(c.islower()      for c in chars))
        self.inc_upper_var.set(any(c.isupper()      for c in chars))
        self.inc_symbols_var.set(any(not c.isalnum() for c in chars))
        _std = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "!@#$%^&*()_+-=[]{}|;':\",./<>?")
        self.extra_chars_var.set("".join(c for c in chars if c not in _std))

        if "length" in cfg:
            self.length_var.set(str(cfg["length"]))
            self.min_var.set(""); self.max_var.set("")
        else:
            self.length_var.set("")
            self.min_var.set(str(cfg.get("min_length", "1")))
            self.max_var.set(str(cfg.get("max_length", "16")))

        nc_list = cfg.get("no_consecutive", [])
        if nc_list:
            nc = nc_list[0]
            self.no_consec_var.set(f"{nc['char']}:{nc['count']}")
        else:
            self.no_consec_var.set("")

        mr = cfg.get("max_repeats", {})
        self.max_repeats_var.set(str(mr.get("digits", "")) if mr else "")

        ent = cfg.get("entropy", {})
        self.entropy_var.set(str(ent.get("min_bits", "")) if ent else "")
        self.no_walks_var.set(bool(cfg.get("keyboard_walk")))

        mut = cfg.get("mutations", {})
        self.mutations_var.set(mut.get("profile", "none") if mut else "none")

        pr = cfg.get("position_rules", {})
        self.must_not_start_var.set(", ".join(pr.get("must_not_start_with", [])))
        self.must_not_end_var.set(", ".join(pr.get("must_not_end_with", [])))
        sw = cfg.get("patterns", {}).get("startswith", [])
        self.must_start_with_var.set(", ".join(sw))

        out = cfg.get("output", {})
        if out.get("path"):   self.output_var.set(out["path"])
        if out.get("format"): self.fmt_var.set(out["format"])
        lim = out.get("max_candidates")
        self.limit_var.set(str(lim) if lim else "")

    # ── Generation ────────────────────────────────────────────────────────────

    def _start_generation(self) -> None:
        if self._running: return

        # Pre-flight checks
        if not self._pre_generate_checks():
            return

        try:
            cfg   = self._build_cfg()
            rules = compile_rules(cfg)
        except (RuleConflictError, ValueError) as exc:
            messagebox.showerror("Configuration Error", str(exc))
            return

        self._last_cfg = cfg
        self._stop_flag.clear()
        self._running = True
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_bar.start(12)
        self.stats_label.configure(text="")
        self.count_label.configure(text="Starting…")
        self.nb.select(0)
        self._log("─" * 44, "HEADING")
        is_wordlist = bool(cfg.get("wordlist", {}).get("custom_path"))
        mode_str = "wordlist" if is_wordlist else "combinatorial"
        charset_preview = rules.charset[:20] + ("…" if len(rules.charset) > 20 else "")
        self._log(f"Mode: {mode_str}  |  charset: {charset_preview}")
        self._log(f"Output: {rules.output_path}")

        threading.Thread(target=self._generate_thread, args=(rules,), daemon=True).start()

    def _stop_generation(self) -> None:
        self._stop_flag.set()
        self._log("Stop requested — finishing current batch…", "WARN")

    def _on_progress(self, written: int, rate: float) -> None:
        if self._stop_flag.is_set(): return
        rate_str = f"{rate/1_000_000:.1f}M" if rate >= 1_000_000 else f"{rate/1_000:.1f}K"
        if written > 0 and written % 50_000 == 0:
            self._log_q.put(("INFO", f"  {written:,} candidates  ({rate_str}/sec)"))
        self.count_label.configure(text=f"{written:,} cands  {rate_str}/s")

    def _generate_thread(self, rules) -> None:
        from .generator import generate
        from .mutation_pipeline import apply_mutations, PROFILES

        try:
            output_path = rules.output_path

            if rules.wordlist_tier != "none" or rules.wordlist_custom_path:
                from .seed_loader import load_wordlist
                enabled = rules.mutations_enabled or PROFILES.get(rules.mutations_profile, [])
                self._log(
                    f"Wordlist mode — {len(enabled)} mutation type(s): "
                    f"{', '.join(enabled) if enabled else 'none (exact match only)'}"
                )
                def source():
                    for base in load_wordlist(rules):
                        if self._stop_flag.is_set(): return
                        yield from apply_mutations(base, enabled, rules.max_expansion)
            else:
                def source():
                    for pw in generate(rules):
                        if self._stop_flag.is_set(): return
                        yield pw

            self._log(f"Writing  →  {output_path}")
            result = run_pipeline(
                source(), rules,
                output_path=output_path,
                compress=rules.compress,
                include_header=rules.include_header,
                show_progress=False,
                progress_callback=self._on_progress,
            )

            lo, hi = result["entropy_range"]
            total, rate = result["total"], result["rate_per_sec"]

            if total == 0:
                self._log(
                    "0 candidates written. Possible causes:\n"
                    "  • Seed words filtered by Length constraint (try clearing Length)\n"
                    "  • Charset doesn't match seed words (try adding Lowercase/Uppercase)\n"
                    "  • Mutations='none' with very restrictive constraints\n"
                    "  • Require-class rules impossible with current charset",
                    "WARN"
                )
            else:
                self._log(f"✓ Done!  {total:,} candidates  →  {output_path}", "OK")
                self._log(
                    f"Entropy: {lo:.1f}–{hi:.1f} bits  |  "
                    f"Rate: {rate:,}/s  |  Dupes removed: {result['duped_count']:,}", "OK"
                )

            self.root.after(0, lambda: self.stats_label.configure(
                text=f"{total:,} candidates  •  {rate:,}/s" if total else "0 candidates — check Log tab"))
            self.root.after(0, lambda: self.count_label.configure(
                text=f"{total:,} done" if total else "0 candidates"))

            if total > 0:
                is_wl = bool(rules.wordlist_custom_path)
                self.root.after(0, lambda: self._add_history(
                    total, output_path, rules.charset, rules,
                    self._last_cfg.copy(), "wordlist" if is_wl else "combinatorial",
                ))

        except Exception as exc:
            self._log(f"Error: {exc}", "ERROR")
        finally:
            self._running = False
            self.root.after(0, self._on_done)

    def _on_done(self) -> None:
        self.progress_bar.stop()
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if self._seed_tmp:
            try: os.unlink(self._seed_tmp)
            except Exception: pass
            self._seed_tmp = None

    # ── History ───────────────────────────────────────────────────────────────

    def _add_history(self, total: int, file: str, charset: str,
                     rules, cfg: dict, mode: str) -> None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        length_str = (str(rules.length) if rules.length
                      else f"{rules.min_length}-{rules.max_length}")
        self._history.append({"ts": ts, "candidates": total, "file": file,
                               "charset": charset[:20], "length": length_str,
                               "mode": mode, "cfg": cfg})
        iid = self.hist_tree.insert("", "end",
            values=(ts, f"{total:,}", file, charset[:20], length_str, mode))
        self._hist_cfg[iid] = cfg
        try: self.hist_empty.pack_forget()
        except Exception: pass
        self.hist_tree.see(iid)

    def _on_hist_select(self, _=None) -> None:
        state = "normal" if self.hist_tree.selection() else "disabled"
        self.hist_reuse_btn.configure(state=state)
        self.hist_open_btn.configure(state=state)

    def _reuse_history(self) -> None:
        sel = self.hist_tree.selection()
        if not sel: return
        self._apply_cfg_to_form(self._hist_cfg.get(sel[0], {}))
        self.nb.select(0)
        self._log("Settings restored from history.", "OK")

    def _open_hist_file(self) -> None:
        sel = self.hist_tree.selection()
        if not sel: return
        vals = self.hist_tree.item(sel[0], "values")
        path = vals[2] if len(vals) > 2 else ""
        if path and os.path.exists(path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception as exc:
                messagebox.showwarning("Cannot Open File", str(exc))
        else:
            messagebox.showwarning("File Not Found", f"Cannot open:\n{path}")

    def _clear_history(self) -> None:
        if not self._history: return
        if messagebox.askyesno("Clear History",
                               f"Remove all {len(self._history)} history entries?"):
            self._history.clear(); self._hist_cfg.clear()
            for iid in self.hist_tree.get_children():
                self.hist_tree.delete(iid)
            self.hist_reuse_btn.configure(state="disabled")
            self.hist_open_btn.configure(state="disabled")
            try: self.hist_empty.pack(pady=6)
            except Exception: pass
            self._log("History cleared.", "INFO")


# ── Helpers outside class ─────────────────────────────────────────────────────

def _bg_key_for(widget: tk.Widget) -> str:
    """Walk up widget hierarchy to find the relevant bg theme key."""
    w = widget
    for _ in range(4):
        p = getattr(w, "master", None)
        if p is None: break
        if isinstance(p, tk.LabelFrame):
            return "bg"
        w = p
    return "bg"


# ── Entry point ───────────────────────────────────────────────────────────────

def run_gui() -> None:
    root = tk.Tk()
    PwgenGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
