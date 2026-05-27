"""
Minimal Tkinter GUI for pwgen.

Layout:
  ┌─────────────────────────────────────────────────┐
  │  Constraints                                    │
  │  [Length] [Min] [Max]  [Charset ▼]              │
  │  [Custom chars]  [No-consec]  [Max repeats]     │
  │  [Min entropy]  [☐ No walks]  [Req classes]     │
  │  [Must not start]  [Must not end]  [Must start] │
  │                                                 │
  │  Hints (plain language)                         │
  │  [multiline free-text hint box]                 │
  │                                                 │
  │  Output                                         │
  │  [File ...]  [Format ▼]  [Mutations ▼]          │
  │  [Preset ▼]  [Limit]                            │
  │                                                 │
  │  [Generate]         [Stop]                      │
  │                                                 │
  │  Progress: ████████░░ 1,234,567 cands           │
  │                                                 │
  │  ╔═════════════ Log ═════════════╗              │
  │  ║  [INFO] Compiling rules…      ║              │
  │  ║  [INFO] Writing out.txt       ║              │
  │  ╚═══════════════════════════════╝              │
  └─────────────────────────────────────────────────┘
"""
from __future__ import annotations
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .hint_parser import parse_hints
from .rule_compiler import compile_rules, RuleConflictError, CHARSETS
from .generator import generate
from .pipeline import run_pipeline


_CHARSETS   = list(CHARSETS.keys()) + ["custom"]
_MUTATIONS  = ["none", "standard", "aggressive"]
_PRESETS    = ["(none)", "numeric_7", "policy_enterprise", "ctf_binary"]
_FORMATS    = ["txt", "csv", "json", "gz"]
_BG         = "#1e1e2e"
_FG         = "#cdd6f4"
_ACCENT     = "#89b4fa"
_BTN_RUN    = "#a6e3a1"
_BTN_STOP   = "#f38ba8"
_ENTRY_BG   = "#313244"
_LOG_BG     = "#11111b"
_FONT       = ("Consolas", 10)
_FONT_H     = ("Consolas", 11, "bold")


class PwgenGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("pwgen — Password List Generator")
        self.root.configure(bg=_BG)
        self.root.resizable(True, True)
        self.root.minsize(680, 600)

        self._stop_flag = threading.Event()
        self._log_q: queue.Queue = queue.Queue()
        self._running = False

        self._build_ui()
        self._poll_log()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = dict(padx=8, pady=4)

        # ── Title bar ──
        tk.Label(
            self.root, text="pwgen  •  Password Candidate Generator",
            font=("Consolas", 13, "bold"), bg=_BG, fg=_ACCENT,
        ).pack(fill="x", **pad)
        tk.Label(
            self.root,
            text="FOR AUTHORIZED SECURITY TESTING ONLY",
            font=("Consolas", 9), bg=_BG, fg=_BTN_STOP,
        ).pack(fill="x", padx=8)

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", pady=6)

        # ── Constraints frame ──
        cf = tk.LabelFrame(
            self.root, text=" Constraints ", font=_FONT_H,
            bg=_BG, fg=_ACCENT, bd=1, relief="groove",
        )
        cf.pack(fill="x", padx=10, pady=4)

        # Row 0: Length / Min / Max / Charset
        self._add_row(cf, 0, [
            ("Length (exact)", "length_var", ""),
            ("Min length",     "min_var",    "1"),
            ("Max length",     "max_var",    "16"),
        ])
        tk.Label(cf, text="Charset", bg=_BG, fg=_FG, font=_FONT).grid(
            row=0, column=6, sticky="e", padx=(12, 2), pady=4)
        self.charset_var = tk.StringVar(value="digits")
        ttk.Combobox(
            cf, textvariable=self.charset_var, values=_CHARSETS,
            state="readonly", width=12, font=_FONT,
        ).grid(row=0, column=7, padx=4, pady=4)

        # Row 1: Custom chars / No-consecutive / Max repeats
        tk.Label(cf, text="Custom chars", bg=_BG, fg=_FG, font=_FONT).grid(
            row=1, column=0, sticky="e", padx=(8,2), pady=4)
        self.custom_chars_var = tk.StringVar()
        tk.Entry(cf, textvariable=self.custom_chars_var, width=14,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=1, column=1, padx=4, pady=4)

        tk.Label(cf, text="No-consec (char:n)", bg=_BG, fg=_FG, font=_FONT).grid(
            row=1, column=2, sticky="e", padx=(12,2), pady=4)
        self.no_consec_var = tk.StringVar()
        tk.Entry(cf, textvariable=self.no_consec_var, width=10,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=1, column=3, padx=4, pady=4)

        tk.Label(cf, text="Max repeats", bg=_BG, fg=_FG, font=_FONT).grid(
            row=1, column=4, sticky="e", padx=(12,2), pady=4)
        self.max_repeats_var = tk.StringVar()
        tk.Entry(cf, textvariable=self.max_repeats_var, width=6,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=1, column=5, padx=4, pady=4)

        # Row 2: Entropy / keyboard walks / require classes
        tk.Label(cf, text="Min entropy (bits)", bg=_BG, fg=_FG, font=_FONT).grid(
            row=2, column=0, sticky="e", padx=(8,2), pady=4)
        self.entropy_var = tk.StringVar()
        tk.Entry(cf, textvariable=self.entropy_var, width=8,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=2, column=1, padx=4, pady=4)

        self.no_walks_var = tk.BooleanVar()
        tk.Checkbutton(
            cf, text="No keyboard walks", variable=self.no_walks_var,
            bg=_BG, fg=_FG, selectcolor=_ENTRY_BG, activebackground=_BG,
            font=_FONT,
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=12, pady=4)

        tk.Label(cf, text="Require classes", bg=_BG, fg=_FG, font=_FONT).grid(
            row=2, column=4, sticky="e", padx=(12,2))
        self.req_upper_var  = tk.BooleanVar()
        self.req_lower_var  = tk.BooleanVar()
        self.req_digit_var  = tk.BooleanVar()
        self.req_symbol_var = tk.BooleanVar()
        for i, (label, var) in enumerate([
            ("upper",  self.req_upper_var),
            ("lower",  self.req_lower_var),
            ("digit",  self.req_digit_var),
            ("symbol", self.req_symbol_var),
        ]):
            tk.Checkbutton(
                cf, text=label, variable=var,
                bg=_BG, fg=_FG, selectcolor=_ENTRY_BG,
                activebackground=_BG, font=_FONT,
            ).grid(row=2, column=5+i, padx=2, pady=4)

        # Row 3: Position rules
        tk.Label(cf, text="Must not start with", bg=_BG, fg=_FG, font=_FONT).grid(
            row=3, column=0, sticky="e", padx=(8,2), pady=4)
        self.must_not_start_var = tk.StringVar()
        tk.Entry(cf, textvariable=self.must_not_start_var, width=14,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=3, column=1, padx=4, pady=4)

        tk.Label(cf, text="Must not end with", bg=_BG, fg=_FG, font=_FONT).grid(
            row=3, column=2, sticky="e", padx=(12,2), pady=4)
        self.must_not_end_var = tk.StringVar()
        tk.Entry(cf, textvariable=self.must_not_end_var, width=14,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=3, column=3, padx=4, pady=4)

        tk.Label(cf, text="Must start with", bg=_BG, fg=_FG, font=_FONT).grid(
            row=3, column=4, sticky="e", padx=(12,2), pady=4)
        self.must_start_with_var = tk.StringVar()
        tk.Entry(cf, textvariable=self.must_start_with_var, width=14,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=3, column=5, padx=4, pady=4)

        tk.Label(
            cf,
            text="(comma-separated values for all position fields)",
            bg=_BG, fg="#6c7086", font=("Consolas", 8),
        ).grid(row=3, column=6, columnspan=3, sticky="w", padx=4)

        # ── Hints frame ──
        hf = tk.LabelFrame(
            self.root, text=" Hints (plain language) ", font=_FONT_H,
            bg=_BG, fg=_ACCENT, bd=1, relief="groove",
        )
        hf.pack(fill="x", padx=10, pady=4)

        tk.Label(
            hf,
            text='One hint per line, e.g:  "7 characters"  |  "starts with admin"  |  '
                 '"no 3 zeros in a row"  |  "must have digit"  |  "no keyboard walk"',
            bg=_BG, fg="#6c7086", font=("Consolas", 8),
        ).pack(anchor="w", padx=6, pady=(4, 0))

        self.hints_text = scrolledtext.ScrolledText(
            hf, bg=_ENTRY_BG, fg=_FG, insertbackground=_FG,
            font=_FONT, height=3, wrap="word",
        )
        self.hints_text.pack(fill="x", padx=6, pady=(2, 6))

        # ── Output frame ──
        of = tk.LabelFrame(
            self.root, text=" Output ", font=_FONT_H,
            bg=_BG, fg=_ACCENT, bd=1, relief="groove",
        )
        of.pack(fill="x", padx=10, pady=4)

        tk.Label(of, text="File", bg=_BG, fg=_FG, font=_FONT).grid(
            row=0, column=0, sticky="e", padx=(8,2), pady=4)
        self.output_var = tk.StringVar(value="wordlist.txt")
        tk.Entry(of, textvariable=self.output_var, width=36,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=0, column=1, padx=4, pady=4)
        tk.Button(
            of, text="Browse…", command=self._browse_output,
            bg=_ENTRY_BG, fg=_FG, activebackground=_ACCENT, font=_FONT,
        ).grid(row=0, column=2, padx=4, pady=4)

        tk.Label(of, text="Format", bg=_BG, fg=_FG, font=_FONT).grid(
            row=0, column=3, sticky="e", padx=(12,2))
        self.fmt_var = tk.StringVar(value="txt")
        ttk.Combobox(
            of, textvariable=self.fmt_var, values=_FORMATS,
            state="readonly", width=6, font=_FONT,
        ).grid(row=0, column=4, padx=4, pady=4)

        tk.Label(of, text="Mutations", bg=_BG, fg=_FG, font=_FONT).grid(
            row=0, column=5, sticky="e", padx=(12,2))
        self.mutations_var = tk.StringVar(value="none")
        ttk.Combobox(
            of, textvariable=self.mutations_var, values=_MUTATIONS,
            state="readonly", width=12, font=_FONT,
        ).grid(row=0, column=6, padx=4, pady=4)

        tk.Label(of, text="Preset", bg=_BG, fg=_FG, font=_FONT).grid(
            row=1, column=0, sticky="e", padx=(8,2), pady=4)
        self.preset_var = tk.StringVar(value="(none)")
        ttk.Combobox(
            of, textvariable=self.preset_var, values=_PRESETS,
            state="readonly", width=18, font=_FONT,
        ).grid(row=1, column=1, padx=4, pady=4)
        tk.Button(
            of, text="Load preset", command=self._load_preset,
            bg=_ENTRY_BG, fg=_FG, activebackground=_ACCENT, font=_FONT,
        ).grid(row=1, column=2, padx=4, pady=4)

        self.limit_var = tk.StringVar()
        tk.Label(of, text="Limit candidates", bg=_BG, fg=_FG, font=_FONT).grid(
            row=1, column=3, sticky="e", padx=(12,2))
        tk.Entry(of, textvariable=self.limit_var, width=12,
                 bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT
                 ).grid(row=1, column=4, padx=4, pady=4)

        # ── Action buttons ──
        bf = tk.Frame(self.root, bg=_BG)
        bf.pack(fill="x", padx=10, pady=6)

        self.run_btn = tk.Button(
            bf, text="▶  Generate", command=self._start_generation,
            bg=_BTN_RUN, fg="#1e1e2e", activebackground="#a6e3a1",
            font=("Consolas", 11, "bold"), width=16,
        )
        self.run_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = tk.Button(
            bf, text="■  Stop", command=self._stop_generation,
            bg=_BTN_STOP, fg="#1e1e2e", activebackground="#f38ba8",
            font=("Consolas", 11, "bold"), width=10, state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(0, 20))

        self.stats_label = tk.Label(
            bf, text="", bg=_BG, fg=_ACCENT, font=_FONT,
        )
        self.stats_label.pack(side="left", fill="x", expand=True)

        # ── Progress bar ──
        pf = tk.Frame(self.root, bg=_BG)
        pf.pack(fill="x", padx=10, pady=2)
        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(
            pf, variable=self.progress_var, mode="indeterminate", length=400,
        )
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.count_label = tk.Label(pf, text="", bg=_BG, fg=_FG, font=_FONT, width=30)
        self.count_label.pack(side="left")

        # ── Log ──
        lf = tk.LabelFrame(
            self.root, text=" Log ", font=_FONT_H,
            bg=_BG, fg=_ACCENT, bd=1, relief="groove",
        )
        lf.pack(fill="both", expand=True, padx=10, pady=6)
        self.log_box = scrolledtext.ScrolledText(
            lf, bg=_LOG_BG, fg=_FG, font=_FONT,
            state="disabled", wrap="word", height=12,
        )
        self.log_box.pack(fill="both", expand=True, padx=4, pady=4)
        self.log_box.tag_config("INFO",    foreground=_FG)
        self.log_box.tag_config("OK",      foreground=_BTN_RUN)
        self.log_box.tag_config("WARN",    foreground="#f9e2af")
        self.log_box.tag_config("ERROR",   foreground=_BTN_STOP)
        self.log_box.tag_config("HEADING", foreground=_ACCENT)

    def _add_row(self, parent, row, fields):
        for col_offset, (label, attr, default) in enumerate(fields):
            tk.Label(parent, text=label, bg=_BG, fg=_FG, font=_FONT).grid(
                row=row, column=col_offset * 2, sticky="e", padx=(8 if col_offset == 0 else 12, 2), pady=4)
            var = tk.StringVar(value=default)
            setattr(self, attr, var)
            tk.Entry(
                parent, textvariable=var, width=6,
                bg=_ENTRY_BG, fg=_FG, insertbackground=_FG, font=_FONT,
            ).grid(row=row, column=col_offset * 2 + 1, padx=4, pady=4)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str, level: str = "INFO") -> None:
        self._log_q.put((level, msg))

    def _poll_log(self) -> None:
        """Drain the log queue into the ScrolledText widget."""
        while not self._log_q.empty():
            level, msg = self._log_q.get_nowait()
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{level}] {msg}\n", level)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(100, self._poll_log)

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("Gzip", "*.gz"),
                       ("CSV", "*.csv"), ("JSON", "*.json"), ("All", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _load_preset(self) -> None:
        import json
        from pathlib import Path
        preset = self.preset_var.get()
        if preset == "(none)":
            return
        preset_path = Path(__file__).parent.parent / "config" / "presets" / f"{preset}.json"
        if not preset_path.exists():
            self._log(f"Preset file not found: {preset_path}", "WARN")
            return
        cfg = json.loads(preset_path.read_text())
        # Apply preset values to UI fields
        if "length" in cfg:
            self.length_var.set(str(cfg["length"]))
        if "charset" in cfg:
            self.charset_var.set(cfg["charset"])
        if "no_consecutive" in cfg and cfg["no_consecutive"]:
            nc = cfg["no_consecutive"][0]
            self.no_consec_var.set(f"{nc['char']}:{nc['count']}")
        if "max_repeats" in cfg and cfg["max_repeats"].get("digits"):
            self.max_repeats_var.set(str(cfg["max_repeats"]["digits"]))
        if "output" in cfg:
            out = cfg["output"]
            if "path" in out:
                self.output_var.set(out["path"])
            if "format" in out:
                self.fmt_var.set(out["format"])
        self._log(f"Preset '{preset}' loaded.", "OK")

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _build_cfg(self) -> dict:
        cfg: dict = {}

        charset = self.charset_var.get()
        if charset == "custom" and self.custom_chars_var.get():
            cfg["charset"] = "custom"
            cfg["custom_chars"] = self.custom_chars_var.get()
        else:
            cfg["charset"] = charset

        if self.length_var.get().strip():
            cfg["length"] = int(self.length_var.get())
        else:
            if self.min_var.get().strip():
                cfg["min_length"] = int(self.min_var.get())
            if self.max_var.get().strip():
                cfg["max_length"] = int(self.max_var.get())

        if self.no_consec_var.get().strip():
            parts = self.no_consec_var.get().split(":")
            char  = parts[0] if parts else "any"
            count = int(parts[1]) if len(parts) > 1 else 3
            cfg["no_consecutive"] = [{"char": char, "count": count}]

        if self.max_repeats_var.get().strip():
            cfg["max_repeats"] = {"digits": int(self.max_repeats_var.get())}

        if self.entropy_var.get().strip():
            cfg["entropy"] = {"min_bits": float(self.entropy_var.get())}

        if self.no_walks_var.get():
            cfg["keyboard_walk"] = {"reject_if_walk_ratio_above": 0.5}

        req = []
        if self.req_upper_var.get():  req.append("upper")
        if self.req_lower_var.get():  req.append("lower")
        if self.req_digit_var.get():  req.append("digit")
        if self.req_symbol_var.get(): req.append("symbol")
        if req:
            cfg.setdefault("charset_options", {})["require_classes"] = req

        if self.mutations_var.get() != "none":
            cfg["mutations"] = {"profile": self.mutations_var.get(), "max_expansion": 50}

        # Position rules
        pos_rules: dict = {}
        if self.must_not_start_var.get().strip():
            pos_rules["must_not_start_with"] = [
                v.strip() for v in self.must_not_start_var.get().split(",") if v.strip()
            ]
        if self.must_not_end_var.get().strip():
            pos_rules["must_not_end_with"] = [
                v.strip() for v in self.must_not_end_var.get().split(",") if v.strip()
            ]
        if pos_rules:
            cfg["position_rules"] = pos_rules

        if self.must_start_with_var.get().strip():
            cfg.setdefault("patterns", {})["startswith"] = [
                v.strip() for v in self.must_start_with_var.get().split(",") if v.strip()
            ]

        # Hints — merge into cfg; explicit GUI fields take priority
        hints_raw = self.hints_text.get("1.0", "end").strip()
        if hints_raw:
            hint_lines = [ln.strip() for ln in hints_raw.splitlines() if ln.strip()]
            hint_cfg = parse_hints(hint_lines)
            for key, val in hint_cfg.items():
                if key not in cfg:
                    cfg[key] = val
                elif key == "patterns" and isinstance(val, dict):
                    # Deep-merge patterns sub-dict
                    for pkey, pval in val.items():
                        cfg["patterns"].setdefault(pkey, pval)
                elif key == "charset_options" and isinstance(val, dict):
                    for okey, oval in val.items():
                        cfg.setdefault("charset_options", {}).setdefault(okey, oval)

        limit = self.limit_var.get().strip()
        cfg["output"] = {
            "format": self.fmt_var.get(),
            "path":   self.output_var.get() or "wordlist.txt",
            "include_header": True,
            "sort_by": "none",
            **({"max_candidates": int(limit)} if limit else {}),
        }

        return cfg

    def _start_generation(self) -> None:
        if self._running:
            return
        try:
            cfg   = self._build_cfg()
            rules = compile_rules(cfg)
        except (RuleConflictError, ValueError) as exc:
            messagebox.showerror("Rule Error", str(exc))
            return

        self._stop_flag.clear()
        self._running = True
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_bar.start(12)
        self.stats_label.configure(text="")
        self.count_label.configure(text="Starting…")
        self._log("─" * 42, "HEADING")
        self._log(f"Compiling rules for charset={rules.charset[:20]}…")

        thread = threading.Thread(target=self._generate_thread, args=(rules,), daemon=True)
        thread.start()

    def _stop_generation(self) -> None:
        self._stop_flag.set()
        self._log("Stop requested — finishing current batch…", "WARN")

    def _on_progress(self, written: int, rate: float) -> None:
        if self._stop_flag.is_set():
            return
        rate_str = f"{rate/1_000_000:.1f}M" if rate >= 1_000_000 else f"{rate/1_000:.1f}K"
        # Only log milestones, not every tick — avoids log spam
        if written > 0 and (written % 50_000 == 0 or rate > 0):
            self._log_q.put(("INFO", f"  {written:,} candidates  ({rate_str}/sec)"))
        self.count_label.configure(text=f"{written:,} cands  {rate_str}/s")

    def _generate_thread(self, rules) -> None:
        from .generator import generate
        from .mutation_pipeline import apply_mutations, PROFILES
        from .filter import passes_all

        try:
            output_path = rules.output_path

            if rules.wordlist_tier != "none" or rules.wordlist_custom_path:
                from .seed_loader import load_wordlist
                enabled = rules.mutations_enabled or PROFILES.get(rules.mutations_profile, [])
                def source():
                    for base in load_wordlist(rules):
                        if self._stop_flag.is_set():
                            return
                        yield from apply_mutations(base, enabled, rules.max_expansion)
            else:
                def source():
                    for pw in generate(rules):
                        if self._stop_flag.is_set():
                            return
                        yield pw

            self._log(f"Writing → {output_path}")
            result = run_pipeline(
                source(), rules,
                output_path=output_path,
                compress=rules.compress,
                include_header=rules.include_header,
                show_progress=False,
                progress_callback=self._on_progress,
            )

            lo, hi = result["entropy_range"]
            self._log(
                f"Done! {result['total']:,} candidates → {output_path}", "OK"
            )
            self._log(
                f"Entropy: {lo:.1f}–{hi:.1f} bits  |  "
                f"Rate: {result['rate_per_sec']:,}/s  |  "
                f"Dupes removed: {result['duped_count']:,}", "OK"
            )
            self.root.after(0, lambda: self.stats_label.configure(
                text=f"{result['total']:,} candidates  •  {result['rate_per_sec']:,}/s"
            ))
            self.root.after(0, lambda: self.count_label.configure(
                text=f"{result['total']:,} done"
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


def run_gui() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TCombobox", fieldbackground="#313244", background="#313244",
                    foreground="#cdd6f4", selectbackground="#45475a")
    style.configure("Horizontal.TProgressbar", troughcolor="#313244",
                    background="#89b4fa", thickness=14)

    app = PwgenGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
