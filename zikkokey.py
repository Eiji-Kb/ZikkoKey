import sys
import subprocess
import time
import ctypes
from datetime import datetime
import ctypes.wintypes
import json
import os
import io

# pythonw で起動すると stdout/stderr が None になり
# Whisper (tqdm等) がクラッシュするため、ダミーに差し替える
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

import tkinter as tk
from tkinter import scrolledtext, messagebox, colorchooser

# ── スプラッシュ（重いライブラリ読み込み前に即表示）──────────────────────
def _show_splash():
    """起動スプラッシュを表示して (root, status_var, msg_dict) を返す"""
    # 言語設定だけ先読み（settings.json が存在すれば）
    _lang = "en"
    try:
        _sf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
        if os.path.exists(_sf):
            with open(_sf, encoding="utf-8") as _f:
                _lang = json.load(_f).get("language", "en")
    except Exception:
        pass
    _msgs = {
        "ja": ("起動中…", "ライブラリ読み込み中…", "初期化中…"),
        "en": ("Starting…",  "Loading libraries…",   "Initializing…"),
    }
    msgs = _msgs.get(_lang, _msgs["ja"])

    root = tk.Tk()
    root.configure(bg="#12121e")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    w, h = 380, 110
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    tk.Label(root, text="ZikkoKey",
             bg="#12121e", fg="#ccccee",
             font=("Yu Gothic", 13, "bold")).pack(pady=(14, 2))
    tk.Label(root, text="Input Pad for AI Coding Agents, with Voice & AI Editing",
             bg="#12121e", fg="#445566",
             font=("Yu Gothic", 8)).pack(pady=(0, 4))
    sv = tk.StringVar(value=msgs[0])
    tk.Label(root, textvariable=sv, bg="#12121e", fg="#6677aa",
             font=("Yu Gothic", 10)).pack()
    root.update()
    return root, sv, msgs

_splash_root, _splash_status, _splash_msgs = _show_splash()
_splash_status.set(_splash_msgs[1])   # "ライブラリ読み込み中…"
_splash_root.update()

import threading
import torch
import numpy as np
import sounddevice as sd
import whisper
import pyperclip
import pyautogui

pyautogui.FAILSAFE = False
_splash_status.set(_splash_msgs[2])   # "初期化中…"
_splash_root.update()

# ── 設定ファイル ──────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "zikkokey.settings.json")

DEFAULT_SETTINGS = {
    "language":           "en",     # "ja" / "en"
    "wrap_mode":          "char",   # "char" / "word" / "none"
    "font_size":          12,
    "model_mode":         "gpu",    # "gpu" / "cpu"
    "cpu_offload_delay":  5,        # 0=すぐ, 1, 5, 10 (分)
    "send_mode":          "slow",   # "normal" / "slow"
    "chunk_size":         100,      # 低速送信時の1チャンク文字数
    "chunk_delay":        0.15,     # 低速送信時のチャンク間待機秒数
    # 編集バックエンド
    "edit_backend":       "claude_cli",  # "claude_cli" / "ollama" / "gemini"
    "ollama_host":        "http://localhost:11434",
    "ollama_model":       "qwen3:8b",
    "gemini_api_key":     "",
    "gemini_model":       "gemini-2.5-flash-lite",
    # 転写精度向上
    "use_user_context":   True,  # user_context.txt をWhisperの文脈ヒントとして渡す
    "use_initial_prompt": False, # 送信履歴をWhisperの文脈ヒントとして渡す
    "whisper_language":   "auto",  # Whisper転写言語: "ja" / "en" / "auto"
    # エディター配色
    "editor_fg":     "#c8c8e0",  # 文字色
    "editor_bg":     "#0d0d1a",  # 背景色
    "editor_cursor": "#8888ff",  # カーソル色
    # 録音中システム音量
    "mic_mute_enabled": True,   # 録音中にシステム音をミュート/低減するか
    "mic_mute_volume":  0,      # 録音中の音量(%) 0=完全ミュート 100=変更なし
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s = DEFAULT_SETTINGS.copy()
                s.update(json.load(f))
                return s
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(s):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

WRAP_MAP = {
    "char": tk.CHAR,
    "word": tk.WORD,
    "none": tk.NONE,
}
# ── 多言語辞書 ───────────────────────────────────────────────
LANG: dict[str, dict[str, str]] = {
    "ja": {
        # ヘッダー
        "btn_settings":      "⚙ 設定",
        # 送信先バー
        "lbl_target":        "送信先:",
        "lbl_no_target":     "（未設定）",
        "btn_pick_win":      "ウィンドウ選択…",
        "btn_screenshot":    "📸",
        "btn_shot_cancel":   "キャンセル",
        "btn_lock_select":  "🔓 フリー",
        "btn_lock_fixed":   "🔒 固定",
        "chk_topmost":      "常に最前面",
        "chk_shutter":      "シャッター音",
        "lbl_shot_bar":     "撮影先:",
        "hint_no_shots":    "（撮影先：をクリックして撮影したいウィンドウを選択）",
        "hint_shot_picking": "→ 撮影するウィンドウをクリックしてください…",
        "dlg_screenshot":    "撮影先ウィンドウを選択",
        "shot_saved":        "📸 保存 → {path}（クリップボードにコピー済み）",
        "shot_no_chrome":    "対象ウィンドウが見つかりません",
        "btn_new_window":    "＋ 新しいウィンドウ",
        "btn_save_file":     "💾 保存",
        "dlg_save_title":    "テキストファイルとして保存",
        "status_saved":      "保存完了 ✓  {path}",
        "status_save_cancel":"保存キャンセル",
        "btn_load_file":     "📂 開く",
        "dlg_load_title":    "テキストファイルを開く",
        "status_loaded":     "読み込み完了 ✓  {path}",
        # 音声入力バー
        "lbl_voice":         "音声:",
        "btn_transcribe":    "🎤 文字起こし [F4]",
        "btn_instruct":      "✏ 編集指示 [F5]",
        "btn_play":          "▷",
        "btn_play_stop":     "■",
        "chk_autoplay":      "自動再生",
        "chk_autosend":      "自動送信",
        "vstatus_playing":   "再生中…",
        # ログ・送信ボタン
        "lbl_log":           "ログ",
        "btn_send":          "送信 [右Ctrl]",
        # ステータス
        "status_ready":      "準備完了",
        "status_sent":       "送信完了 ✓",
        "status_sending":    "送信中...",
        "status_error":      "エラー",
        "status_restored":   "復元完了 ✓  (履歴残: {n}件 / Ctrl+Y で進む)",
        "status_redo":       "やり直し ✓  (さらに{n}件進める)",
        "status_redo_done":  "やり直し完了 ✓",
        # 音声ステータス
        "vstatus_loading_b": "モデル読み込み中",
        "vstatus_loading_db":"モデル読み込み中 ({d})",
        "vstatus_fail":      "読み込み失敗",
        "vstatus_rec":       "● 録音中…",
        "vstatus_trans":     "転写中…",
        "vstatus_silent":    "(無音)",
        "vstatus_done":      "転写完了 ✓",
        "vstatus_err":       "転写エラー: {e}",
        "vstatus_editing":   "指示を受信、編集中…",
        "vstatus_edit_done": "編集完了 ✓",
        "vstatus_edit_fail": "編集失敗",
        "vstatus_edit_err":  "編集エラー: {msg}",
        "vstatus_inst_rec":  "✏️ 指示録音中… (離して確定)",
        "vstatus_cpu_wait":  "💤 CPU待機中",
        "vstatus_gpu_back":  "GPUへ復帰中…",
        "vstatus_gpu_back_b":"GPUへ復帰中",
        "vstatus_other_rec": "他のウィンドウで録音中",
        "chk_input_mute":    "入力時音ミュート",
        # ゲージ（レート制限）
        "rate_5h":           "現在のセッション（5時間）",
        "rate_7d":           "週間制限（7日間）",
        "rate_reset":        "残り {h}:{m:02d}",
        "rate_reset_at":     "{hh}:{mm:02d}（{dow}）リセット",
        "rate_reset_days":   ["月","火","水","木","金","土","日"],
        "rate_soon":         "まもなくリセット",
        "rate_reset_done":   "リセット済み",
        # 設定ダイアログ
        "dlg_settings":      "設定",
        "sec_language":      "言語 / Language",
        "lbl_ui_lang":       "UI言語:",
        "sec_color":         "エディター配色",
        "color_fg":          "文字色",
        "color_bg":          "背景色",
        "color_cursor":      "カーソル色",
        "btn_color_pick":    "選択…",
        "lbl_preset":        "プリセット:",
        "preset_dark":       "ダーク（既定）",
        "preset_green":      "グリーン",
        "preset_amber":      "アンバー",
        "color_dlg_title":   "色を選択",
        "sec_wrap":          "折り返しモード",
        "wrap_char":         "文字単位（日本語向け）",
        "wrap_word":         "単語単位（英語向け）",
        "wrap_none":         "折り返しなし",
        "sec_fontsize":      "フォントサイズ",
        "lbl_pt":            "pt",
        "sec_mic_mute":      "録音中のシステム音量",
        "chk_mic_mute":      "録音中にシステム音をミュート / 低減する",
        "lbl_mic_volume":    "録音中の音量:",
        "lbl_mic_vol_off":   "無音",
        "lbl_mic_vol_on":    "変更なし",
        "log_mic_mute":      "[MIC] システム音 {cur}% → {tgt}% (×{ratio}%)",
        "log_mic_restore":   "[MIC] システム音復元",
        "log_mic_mute_fail": "[MIC] ミュート失敗: {e}",
        "log_mic_rest_fail": "[MIC] 復元失敗: {e}",
        "log_fp16_retry":    "[VOICE] fp16エラー検出 → fp32でリトライ",
        "log_fp32_reload":   "[VOICE] fp32でもNaN → モデルを再ロード",
        "log_edit_empty":    "[EDIT] 空の応答: {detail}",
        "log_gpu_skip":      "[GPU] 復帰処理が既に実行中のためスキップ",
        "log_load_fail_enc": "[LOAD] 読み込み失敗: 文字コード不明 {path}",
        "sec_transcript":    "聞き取り精度",
        "chk_prompt_quick":  "文脈ヒント",
        "chk_user_ctx":      "ユーザー辞書（user_context.txt）があれば使う",
        "chk_prompt":        "文脈ヒント（送信履歴・現在の入力をWhisperに渡す）",
        "chk_prompt_note":   "※文脈ヒントは精度が向上する場合がありますが、内容によっては却って混乱して精度が低下したり空文字になる場合があります。",
        "lbl_whisper_lang":  "聞き取り言語:",
        "wlang_ja":          "日本語",
        "wlang_en":          "English",
        "wlang_auto":        "自動検出",
        "sec_model_mode":    "音声モデル管理（GPU / メモリ）",
        "mode_gpu":          "常時GPU  ─  転写後もVRAMに保持（最速）",
        "mode_cpu":          "CPU待機  ─  転写後にVRAMを解放（復帰数秒程度）",
        "lbl_offload_time":  "オフロードまでの時間:",
        "delay_0":           "すぐ",
        "delay_1":           "1分後",
        "delay_5":           "5分後",
        "delay_10":          "10分後",
        "sec_send_mode":     "送信モード",
        "send_normal":       "通常  ─  一括貼り付け（高速）",
        "send_slow":         "低速  ─  チャンク分割貼り付け（長文・Claude Code向け）",
        "lbl_chunk":         "チャンク:",
        "lbl_interval":      "文字  間隔:",
        "lbl_sec":           "秒",
        "sec_backend":       "編集指示バックエンド",
        "backend_cli":       "Claude CLI  ─  claude コマンド使用（追加費用なし）",
        "backend_ollama":    "Ollama  ─  ローカルLLM（無料）",
        "backend_gemini":    "Google Gemini API  ─  要APIキー（無料枠あり）",
        "frm_gemini":        "Google Gemini API 設定",
        "err_no_gemini_pkg": "google-genai が未インストールです。\npip install google-genai を実行してください。",
        "frm_ollama":        "Ollama 設定",
        "lbl_model_name":    "モデル名:",
        "lbl_host":          "ホスト:",
        "lbl_api_key":       "APIキー:",
        "lbl_model":         "モデル:",
        "btn_apply":         "適用",
        # ウィンドウ選択ダイアログ
        "dlg_pick_win":      "送信先ウィンドウを選択",
        "btn_select":        "選択",
        # ダイアログ（messagebox）
        "dlg_no_wins":       "ウィンドウが見つかりません",
        "dlg_no_wins_t":     "情報",
        "dlg_no_target":     "「ウィンドウ選択…」で Claude Code のターミナルを選んでください。",
        "dlg_no_target_t":   "送信先未設定",
        "dlg_no_base_t":     "元テキストなし",
        "dlg_no_base":       "テキストエリアに編集対象の文章を入力してください。",
        "dlg_whisper_err_t": "Whisper エラー",
        "dlg_whisper_err":   "モデルの読み込みに失敗しました:\n{msg}",
        "dlg_send_err_t":    "送信エラー",
        "dlg_clip_err_t":    "エラー",
        "err_no_claude":     "claude コマンドが見つかりません。\nターミナルで 'where claude' を実行してパスを確認し、\n設定の Claude CLI パスに入力してください。",
        "err_no_api_key":    "APIキーが設定されていません",
        "err_ollama_down":   "Ollama が起動していません。\nOllama アプリを起動してから再試行してください。",
        "err_ollama_model":  "モデル '{model}' が見つかりません。\nターミナルで 'ollama pull {model}' を実行してからお試しください。",
        "err_ollama_other":  "Ollama エラー: {msg}",
        # ログメッセージ
        "log_yes":               "あり",
        "log_no":                "なし",
        "log_voice_load":        "[VOICE] load_model({model}, device={device}) 開始",
        "log_voice_load_ok":     "[VOICE] 読み込み完了 ({device})",
        "log_voice_load_fail":   "[VOICE] {device} 失敗: {err}",
        "log_voice_trans_start": "[VOICE] 転写開始 フレーム数={n}",
        "log_voice_no_audio":    "[VOICE] 音声なし（録音できていない可能性）",
        "log_voice_audio_len":   "[VOICE] 音声長={sec:.1f}秒",
        "log_voice_audio_err":   "[VOICE] 音声結合エラー: {e}",
        "log_voice_transcribe":  "[VOICE] transcribe開始 fp16={fp16} prompt={prompt}",
        "log_voice_silent_skip": "[VOICE] 無音と判定してスキップ (no_speech_prob={prob:.2f})",
        "log_voice_halluc":      "[VOICE] ハルシネーション or 空文字、スキップ: {text}",
        "log_voice_result":      "[VOICE] 転写結果: {text}",
        "log_voice_err":         "[VOICE] 転写エラー: {e}",
        "log_edit_inst":         "[EDIT] 指示: {text}",
        "log_edit_done":         "[EDIT] 完了: {text}",
        "log_edit_cli":          "[EDIT] Claude CLI: {path}",
        "log_edit_prompt":       "[EDIT] プロンプト先頭: {text}",
        "log_edit_ollama":       "[EDIT] Ollama ({model}) で処理中…",
        "log_edit_api":          "[EDIT] Claude API ({model}) で処理中…",
        "log_edit_gemini":       "[EDIT] Gemini ({model}) で処理中…",
        "log_edit_err":          "[EDIT] エラー: {e}",
        "log_edit_mode":         "[EDIT] 指示モード録音開始",
        "log_gpu_sched":         "[GPU] オフロード予約 ({mode}, {label})",
        "log_gpu_to_cpu":        "[GPU] モデルをCPUへ移動（VRAM解放）",
        "log_gpu_err":           "[GPU] オフロードエラー: {e}",
        "log_gpu_restore":       "[GPU] モデルをGPUへ復帰",
        "log_gpu_restore_fail":  "[GPU] GPU復帰失敗、CPUで継続: {e}",
        "log_undo_restore":      "[UNDO] 送信履歴から復元 ({n}文字、残り{remaining}件)",
        "log_undo_empty":        "[UNDO] 履歴なし",
        "log_undo_limit":        "[UNDO] これ以上戻れません",
        "log_redo_advance":      "[REDO] 一つ進む ({n}文字、さらに進める: {remaining}件)",
        "log_redo_limit":        "[REDO] これ以上進めません",
        "log_slow":              "[SLOW] {n}チャンクに分割（{size}文字×{delay}秒）",
        "log_send_empty":        "(空→改行のみ)",
        "log_send_chars":        "{n}文字",
        "log_send":              "[SEND] {info} → {target}",
        "log_send_ok":           "[OK] 送信完了",
        "log_err_clip":          "[ERR] クリップボード: {e}",
        "log_err_send":          "[ERR] 送信失敗: {e}",
        "log_key_send":          "[KEY] {key} 送信",
        "log_lock_on":           "[LOCK] ロック中 – 送信先を固定",
        "log_lock_off":          "[LOCK] 解除中 – 次にクリックしたウィンドウを送信先に設定",
        "log_auto_target":       "[AUTO] 送信先: {title}",
        "log_set_target":        "[SET] 送信先: {title}",
        "log_set_apply":         "[SET] 折り返し={wrap}  サイズ={size}pt  モデル管理={mode}",
        "log_rctrl":             "[KEY] 右Ctrl 検出",
        "html_detected":         "HTMLが検出されました",
        "html_ask":              "出力がHTMLです。ブラウザでプレビューしますか？\n\n「はい」→ブラウザで開く\n「いいえ」→テキストエリアに挿入",
        "log_html_preview":      "[HTML] ブラウザでプレビュー: {path}",
    },
    "en": {
        # Header
        "btn_settings":      "⚙ Settings",
        # Target bar
        "lbl_target":        "Target:",
        "lbl_no_target":     "(not set)",
        "btn_pick_win":      "Select window…",
        "btn_screenshot":    "📸",
        "btn_shot_cancel":   "Cancel",
        "btn_lock_select":  "🔓 Free",
        "btn_lock_fixed":   "🔒 Fixed",
        "chk_topmost":      "Always on top",
        "chk_shutter":      "Shutter sound",
        "lbl_shot_bar":     "Capture:",
        "hint_no_shots":    "(Click 'Capture:' then select a window)",
        "hint_shot_picking": "→ Click the window you want to capture…",
        "dlg_screenshot":    "Select capture target window",
        "shot_saved":        "📸 Saved → {path} (copied to clipboard)",
        "shot_no_chrome":    "No windows found",
        "btn_new_window":    "+ New window",
        "btn_save_file":     "💾 Save",
        "dlg_save_title":    "Save as text file",
        "status_saved":      "Saved ✓  {path}",
        "status_save_cancel":"Save cancelled",
        "btn_load_file":     "📂 Open",
        "dlg_load_title":    "Open text file",
        "status_loaded":     "Loaded ✓  {path}",
        # Voice bar
        "lbl_voice":         "Audio:",
        "btn_transcribe":    "🎤 Transcribe [F4]",
        "btn_instruct":      "✏ Edit instruct [F5]",
        "btn_play":          "▷",
        "btn_play_stop":     "■",
        "chk_autoplay":      "Auto-play",
        "chk_autosend":      "Auto-send",
        "vstatus_playing":   "Playing…",
        # Log / send
        "lbl_log":           "Log",
        "btn_send":          "Send [R-Ctrl]",
        # Status
        "status_ready":      "Ready",
        "status_sent":       "Sent ✓",
        "status_sending":    "Sending...",
        "status_error":      "Error",
        "status_restored":   "Restored ✓  ({n} left / Ctrl+Y to redo)",
        "status_redo":       "Redo ✓  ({n} more)",
        "status_redo_done":  "Redo done ✓",
        # Voice status
        "vstatus_loading_b": "Loading model",
        "vstatus_loading_db":"Loading model ({d})",
        "vstatus_fail":      "Load failed",
        "vstatus_rec":       "● Recording…",
        "vstatus_trans":     "Transcribing…",
        "vstatus_silent":    "(silent)",
        "vstatus_done":      "Transcription done ✓",
        "vstatus_err":       "Transcription error: {e}",
        "vstatus_editing":   "Instruction received, editing…",
        "vstatus_edit_done": "Edit done ✓",
        "vstatus_edit_fail": "Edit failed",
        "vstatus_edit_err":  "Edit error: {msg}",
        "vstatus_inst_rec":  "✏️ Recording instruction… (release to confirm)",
        "vstatus_cpu_wait":  "💤 CPU standby",
        "vstatus_gpu_back":  "Restoring to GPU…",
        "vstatus_gpu_back_b":"Restoring to GPU",
        "vstatus_other_rec": "Recording in another window",
        "chk_input_mute":    "Mute while recording",
        # Rate limit gauges
        "rate_5h":           "Current session (5h)",
        "rate_7d":           "Weekly limit (7d)",
        "rate_reset":        "Resets in {h}:{m:02d}",
        "rate_reset_at":     "Reset {dow} {hh}:{mm:02d}",
        "rate_reset_days":   ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        "rate_soon":         "Resetting soon",
        "rate_reset_done":   "Reset done",
        # Settings dialog
        "dlg_settings":      "Settings",
        "sec_language":      "Language / 言語",
        "lbl_ui_lang":       "UI language:",
        "sec_color":         "Editor colors",
        "color_fg":          "Text",
        "color_bg":          "Background",
        "color_cursor":      "Cursor",
        "btn_color_pick":    "Pick…",
        "lbl_preset":        "Presets:",
        "preset_dark":       "Dark (default)",
        "preset_green":      "Green",
        "preset_amber":      "Amber",
        "color_dlg_title":   "Choose color",
        "sec_wrap":          "Word wrap",
        "wrap_char":         "Character (recommended for Japanese)",
        "wrap_word":         "Word",
        "wrap_none":         "No wrap",
        "sec_fontsize":      "Font size",
        "lbl_pt":            "pt",
        "sec_mic_mute":      "System volume during recording",
        "chk_mic_mute":      "Mute / reduce system audio while recording",
        "lbl_mic_volume":    "Volume during recording:",
        "lbl_mic_vol_off":   "Silent",
        "lbl_mic_vol_on":    "Unchanged",
        "log_mic_mute":      "[MIC] System audio {cur}% → {tgt}% (×{ratio}%)",
        "log_mic_restore":   "[MIC] System audio restored",
        "log_mic_mute_fail": "[MIC] Mute failed: {e}",
        "log_mic_rest_fail": "[MIC] Restore failed: {e}",
        "log_fp16_retry":    "[VOICE] fp16 error detected → retrying with fp32",
        "log_fp32_reload":   "[VOICE] NaN in fp32 → reloading model",
        "log_edit_empty":    "[EDIT] Empty response: {detail}",
        "log_gpu_skip":      "[GPU] Restore already in progress, skipping",
        "log_load_fail_enc": "[LOAD] Failed: unknown encoding {path}",
        "sec_transcript":    "Recognition accuracy",
        "chk_prompt_quick":  "Context hint",
        "chk_user_ctx":      "Use user dictionary (user_context.txt) if available",
        "chk_prompt":        "Context hint (pass input history & current text to Whisper)",
        "chk_prompt_note":   "※Context hint may improve accuracy, but depending on content may cause confusion, reduced accuracy, or empty results.",
        "lbl_whisper_lang":  "Listening language:",
        "wlang_ja":          "日本語",
        "wlang_en":          "English",
        "wlang_auto":        "Auto-detect",
        "sec_model_mode":    "Voice model (GPU / memory)",
        "mode_gpu":          "Always GPU  ─  keep in VRAM after transcription (fastest)",
        "mode_cpu":          "CPU standby  ─  release VRAM after transcription (restore: a few sec)",
        "lbl_offload_time":  "Offload after:",
        "delay_0":           "Immediately",
        "delay_1":           "1 min",
        "delay_5":           "5 min",
        "delay_10":          "10 min",
        "sec_send_mode":     "Send mode",
        "send_normal":       "Normal  ─  paste at once (fast)",
        "send_slow":         "Slow  ─  chunked paste (for long text / Claude Code)",
        "lbl_chunk":         "Chunk:",
        "lbl_interval":      "chars  interval:",
        "lbl_sec":           "sec",
        "sec_backend":       "Edit instruction backend",
        "backend_cli":       "Claude CLI  ─  uses claude command (no extra cost)",
        "backend_ollama":    "Ollama  ─  local LLM (free)",
        "backend_gemini":    "Google Gemini API  ─  requires API key (free tier available)",
        "frm_gemini":        "Google Gemini API settings",
        "err_no_gemini_pkg": "google-genai is not installed.\nRun: pip install google-genai",
        "frm_ollama":        "Ollama settings",
        "lbl_model_name":    "Model:",
        "lbl_host":          "Host:",
        "lbl_api_key":       "API key:",
        "lbl_model":         "Model:",
        "btn_apply":         "Apply",
        # Window picker
        "dlg_pick_win":      "Select target window",
        "btn_select":        "Select",
        # Dialogs
        "dlg_no_wins":       "No windows found",
        "dlg_no_wins_t":     "Info",
        "dlg_no_target":     "Please select a Claude Code terminal via 'Select window…'.",
        "dlg_no_target_t":   "No target set",
        "dlg_no_base_t":     "No base text",
        "dlg_no_base":       "Please enter text to edit in the text area.",
        "dlg_whisper_err_t": "Whisper error",
        "dlg_whisper_err":   "Failed to load model:\n{msg}",
        "dlg_send_err_t":    "Send error",
        "dlg_clip_err_t":    "Error",
        "err_no_claude":     "claude command not found.\nRun 'where claude' in terminal to find the path\nand enter it in Settings > Claude CLI path.",
        "err_no_api_key":    "API key is not set",
        "err_ollama_down":   "Ollama is not running.\nPlease start the Ollama app and try again.",
        "err_ollama_model":  "Model '{model}' not found.\nRun 'ollama pull {model}' in terminal first.",
        "err_ollama_other":  "Ollama error: {msg}",
        # Log messages
        "log_yes":               "yes",
        "log_no":                "no",
        "log_voice_load":        "[VOICE] load_model({model}, device={device}) start",
        "log_voice_load_ok":     "[VOICE] Load complete ({device})",
        "log_voice_load_fail":   "[VOICE] {device} failed: {err}",
        "log_voice_trans_start": "[VOICE] Transcribe start frames={n}",
        "log_voice_no_audio":    "[VOICE] No audio (recording may have failed)",
        "log_voice_audio_len":   "[VOICE] Audio length={sec:.1f}s",
        "log_voice_audio_err":   "[VOICE] Audio concat error: {e}",
        "log_voice_transcribe":  "[VOICE] transcribe fp16={fp16} prompt={prompt}",
        "log_voice_silent_skip": "[VOICE] Silent, skipping (no_speech_prob={prob:.2f})",
        "log_voice_halluc":      "[VOICE] Hallucination or empty, skipping: {text}",
        "log_voice_result":      "[VOICE] Result: {text}",
        "log_voice_err":         "[VOICE] Transcribe error: {e}",
        "log_edit_inst":         "[EDIT] Instruction: {text}",
        "log_edit_done":         "[EDIT] Done: {text}",
        "log_edit_cli":          "[EDIT] Claude CLI: {path}",
        "log_edit_prompt":       "[EDIT] Prompt start: {text}",
        "log_edit_ollama":       "[EDIT] Ollama ({model}) processing…",
        "log_edit_api":          "[EDIT] Claude API ({model}) processing…",
        "log_edit_gemini":       "[EDIT] Gemini ({model}) processing…",
        "log_edit_err":          "[EDIT] Error: {e}",
        "log_edit_mode":         "[EDIT] Instruction mode recording started",
        "log_gpu_sched":         "[GPU] Offload scheduled ({mode}, {label})",
        "log_gpu_to_cpu":        "[GPU] Model moved to CPU (VRAM released)",
        "log_gpu_err":           "[GPU] Offload error: {e}",
        "log_gpu_restore":       "[GPU] Model restored to GPU",
        "log_gpu_restore_fail":  "[GPU] GPU restore failed, continuing on CPU: {e}",
        "log_undo_restore":      "[UNDO] Restored from history ({n} chars, {remaining} remaining)",
        "log_undo_empty":        "[UNDO] No history",
        "log_undo_limit":        "[UNDO] Cannot go back further",
        "log_redo_advance":      "[REDO] Advanced ({n} chars, {remaining} more)",
        "log_redo_limit":        "[REDO] Cannot go forward further",
        "log_slow":              "[SLOW] Split into {n} chunks ({size} chars × {delay}s)",
        "log_send_empty":        "(empty→newline only)",
        "log_send_chars":        "{n} chars",
        "log_send":              "[SEND] {info} → {target}",
        "log_send_ok":           "[OK] Send complete",
        "log_err_clip":          "[ERR] Clipboard: {e}",
        "log_err_send":          "[ERR] Send failed: {e}",
        "log_key_send":          "[KEY] {key} sent",
        "log_lock_on":           "[LOCK] Locked – target fixed",
        "log_lock_off":          "[LOCK] Unlocked – next clicked window set as target",
        "log_auto_target":       "[AUTO] Target: {title}",
        "log_set_target":        "[SET] Target: {title}",
        "log_set_apply":         "[SET] Wrap={wrap}  Size={size}pt  Model={mode}",
        "log_rctrl":             "[KEY] R-Ctrl detected",
        "html_detected":         "HTML detected",
        "html_ask":              "The output looks like HTML. Open in browser?\n\n'Yes' → open in browser\n'No' → insert into text area",
        "log_html_preview":      "[HTML] Browser preview: {path}",
    },
}

# 現在の言語（起動時は settings から読む）
_current_lang: str = "ja"
# StringVar レジストリ（live switching 用）
_svars: dict[str, "tk.StringVar"] = {}

def t(key: str, **kw) -> str:
    """翻訳ヘルパー。キーに対応する文字列を返す。フォーマット引数も受け取れる"""
    s = LANG.get(_current_lang, LANG["ja"]).get(key, LANG["ja"].get(key, f"[{key}]"))
    return s.format(**kw) if kw else s

def sv(key: str) -> "tk.StringVar":
    """翻訳済み StringVar を返す（なければ生成）。言語切り替え時に一括更新される"""
    if key not in _svars:
        _svars[key] = tk.StringVar(value=t(key))
    return _svars[key]

_lang_callbacks = []  # 言語切替時に呼ぶコールバック

def switch_lang(lang: str) -> None:
    """言語を切り替えてすべての StringVar を更新する"""
    global _current_lang
    if lang not in LANG:
        return
    _current_lang = lang
    for key, var in _svars.items():
        var.set(t(key))
    for cb in _lang_callbacks[:]:
        try:
            cb()
        except Exception:
            pass

# WRAP_LABEL は t() で取得するよう変更（後方互換のため dict も残す）
def get_wrap_label(mode: str) -> str:
    return t(f"wrap_{mode}")

# ── Windows API ──────────────────────────────────────────────
user32 = ctypes.windll.user32

def get_all_windows():
    found = []
    def handler(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                found.append((hwnd, buf.value))
        return True
    Proc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                               ctypes.wintypes.HWND,
                               ctypes.wintypes.LPARAM)
    user32.EnumWindows(Proc(handler), 0)
    return found

def activate_hwnd(hwnd):
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)

# ── アプリ共用状態 ───────────────────────────────────────────
class _AppState:
    """複数ウィンドウ間で共用するリソース（Whisperモデル・録音排他制御）"""
    def __init__(self):
        self.whisper_model      = None
        self.whisper_device     = "cpu"
        self.offload_timer      = None   # _splash_root.after() の ID
        self.recording_owner    = None   # 現在録音中の InputWindow
        self.last_record_window = None   # GPU/オフロード状態表示用
        self.windows            = []     # 開いているすべての InputWindow
        self.prev_audio         = None   # 録音前の {"mute": int, "vol": float}（None=未保存）
        self.audio_vol          = None   # キャッシュ済み POINTER(IAudioEndpointVolume)
        self.audio_iface        = None   # interface を生存させる参照
        self.gpu_restoring      = False  # GPU復帰中フラグ（二重実行防止）
        self.transcribe_lock    = threading.Lock()  # transcribe() 同時実行防止

_g = _AppState()

# ── メインウィンドウ ─────────────────────────────────────────
class InputWindow:
    HISTORY_MAX = 20

    def __init__(self, root=None):
        self.target_hwnd  = None
        self.target_title = ""
        self.settings     = load_settings()
        switch_lang(self.settings.get("language", "ja"))  # 起動時に言語を適用
        self.locked        = False  # True=ロック中, False=解除中（クリックで自動選択）
        self.sent_history  = []    # 送信済みテキストの履歴（新しい順）
        self._redo_stack      = []   # Ctrl+Y 用：undo で行き過ぎた分を積むスタック
        self._screenshot_target  = None   # 📸 撮影先ウィンドウ (hwnd, title) または None
        self._shutter_sound_var  = tk.BooleanVar(value=True)  # シャッター音ON/OFF
        self._shot_picking       = False  # 📸 ピッキングモード中フラグ
        self._shot_pick_id       = None   # after() キャンセル用 ID
        self._shot_add_btn       = None   # 📸 ボタンへの参照
        self._shot_suppress_until = 0.0  # この時刻まで _poll_foreground の登録を抑制
        # 前回終了時の initial_prompt を読み込む（起動直後から文脈ヒントを有効化）
        _prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "initial_prompt.txt")
        try:
            _saved = open(_prompt_path, encoding="utf-8").read().strip()
            self._initial_prompt_cache = _saved if _saved else None
        except FileNotFoundError:
            self._initial_prompt_cache = None
        # user_context.txt を起動時に読み込む
        self._user_ctx_content = ""
        self._user_ctx_mtime   = 0.0
        self._reload_user_ctx_if_changed()
        # 音声入力（録音・再生はウィンドウごと）
        # NOTE: whisper_model / whisper_device / offload_timer は _g に集約
        self.recording      = False
        self.voice_mode     = "input"  # "input" / "edit"
        self._ptt_held      = False    # 文字起こしボタンが押し続けられているか
        self._instr_held    = False    # 指令ボタンが押し続けられているか
        self.audio_frames   = []
        self.last_audio     = None   # 最後に録音した音声（再生用）
        self._playing       = False  # sd.play 再生中フラグ
        self.SAMPLE_RATE    = 16000
        self.WHISPER_MODEL  = "turbo"
        self.auto_play_var  = tk.BooleanVar(value=False)
        self.auto_send_var  = tk.BooleanVar(value=self.settings.get("auto_send", False))

        if root is None:
            # 初回ウィンドウ：スプラッシュを Toplevel に置き換え
            self.root = tk.Toplevel(_splash_root)
            _splash_root.withdraw()
        else:
            self.root = root

        self.root.title("ZikkoKey")
        self.root.geometry("800x660")
        self.root.resizable(True, True)
        self.root.configure(bg="#12121e")
        self._topmost_var = tk.BooleanVar(value=self.settings.get("topmost", True))
        self.root.attributes("-topmost", self._topmost_var.get())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        _g.windows.append(self)

        self._build_ui()
        # デフォルトが解除状態なので起動時からフォアグラウンド監視を開始
        self.root.after(500, self._poll_foreground)
        # モデルが未ロードなら先読み
        if _g.whisper_model is None:
            threading.Thread(target=self._load_model, daemon=True).start()
        # レート制限の定期読み込み開始
        self._poll_rate_limits()

    # ── UI構築 ───────────────────────────────────────────────
    def _build_ui(self):
        # ヘッダー
        hdr = tk.Frame(self.root, bg="#1a1a30", pady=8)
        hdr.pack(fill=tk.X)
        tk.Checkbutton(hdr, textvariable=sv("chk_topmost"),
                       variable=self._topmost_var,
                       command=self._toggle_topmost,
                       bg="#1a1a30", fg="#aaaacc", selectcolor="#1a1a30",
                       activebackground="#1a1a30", activeforeground="#ccccee",
                       font=("Yu Gothic", 9), cursor="hand2"
                       ).pack(side=tk.LEFT, padx=12)
        tk.Button(hdr, textvariable=sv("btn_settings"),
                  command=self._open_settings,
                  bg="#2a2a44", fg="#ccccee",
                  font=("Yu Gothic", 9),
                  relief=tk.FLAT, padx=10, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=10)
        tk.Button(hdr, textvariable=sv("btn_new_window"),
                  command=self._open_new_window,
                  bg="#1e3a1e", fg="#88dd88",
                  font=("Yu Gothic", 9),
                  relief=tk.FLAT, padx=10, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=(0, 4))
        tk.Button(hdr, textvariable=sv("btn_save_file"),
                  command=self._save_to_file,
                  bg="#2a3a2a", fg="#aaddaa",
                  font=("Yu Gothic", 9),
                  relief=tk.FLAT, padx=10, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=(0, 4))
        tk.Button(hdr, textvariable=sv("btn_load_file"),
                  command=self._load_from_file,
                  bg="#2a3a2a", fg="#aaddaa",
                  font=("Yu Gothic", 9),
                  relief=tk.FLAT, padx=10, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=(0, 4))
        # ターゲット選択バー
        tbar = tk.Frame(self.root, bg="#1e1e38", pady=5)
        tbar.pack(fill=tk.X)

        # 右端を先にpack → 左側が長くなっても必ず表示される
        tk.Button(tbar, textvariable=sv("btn_pick_win"),
                  command=self._pick_window,
                  bg="#334466", fg="white",
                  font=("Yu Gothic", 9),
                  relief=tk.FLAT, padx=10, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=10)

        # 鍵アイコン（ロック／解除トグル）
        self.lock_btn = tk.Button(tbar, text=t("btn_lock_select"),
                                  command=self._toggle_lock,
                                  bg="#334466", fg="#aaccff",
                                  font=("Yu Gothic", 10),
                                  relief=tk.FLAT, cursor="hand2", bd=0, padx=6)
        self.lock_btn.pack(side=tk.LEFT, padx=(8, 2))

        tk.Label(tbar, textvariable=sv("lbl_target"), bg="#1e1e38", fg="#aaaacc",
                 font=("Yu Gothic", 10)).pack(side=tk.LEFT, padx=(2, 4))
        self.target_var = tk.StringVar(value=t("lbl_no_target"))
        tk.Label(tbar, textvariable=self.target_var,
                 bg="#1e1e38", fg="#66aaff",
                 font=("Yu Gothic", 10)).pack(side=tk.LEFT)

        # 音声入力バー（1行目：録音ボタン群）
        vbar = tk.Frame(self.root, bg="#1a1a30", pady=5)
        vbar.pack(fill=tk.X)
        tk.Label(vbar, textvariable=sv("lbl_voice"), bg="#1a1a30", fg="#aaaacc",
                 font=("Yu Gothic", 9)).pack(side=tk.LEFT, padx=(10, 6))
        # 文字起こしボタン（押し続け→離す で録音＆転写）
        self.transcribe_btn = tk.Button(vbar, textvariable=sv("btn_transcribe"),
                                        bg="#2a2a44", fg="#ccccee",
                                        font=("Yu Gothic", 9),
                                        relief=tk.FLAT, padx=10, cursor="hand2")
        self.transcribe_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.transcribe_btn.bind("<ButtonPress-1>",   self._transcribe_start)
        self.transcribe_btn.bind("<ButtonRelease-1>", self._transcribe_stop)
        # 指令ボタン（押し続け→離す で録音＆指令実行）
        self.instruct_btn = tk.Button(vbar, textvariable=sv("btn_instruct"),
                                      bg="#2a2a44", fg="#ccccee",
                                      font=("Yu Gothic", 9),
                                      relief=tk.FLAT, padx=10, cursor="hand2")
        self.instruct_btn.pack(side=tk.LEFT)
        self.instruct_btn.bind("<ButtonPress-1>",   self._instruct_start)
        self.instruct_btn.bind("<ButtonRelease-1>", self._instruct_stop)
        # 文脈ヒント クイックトグル（リリース時は非表示）
        self._prompt_var = tk.BooleanVar(value=self.settings.get("use_initial_prompt", False))
        # def _on_prompt_toggle():
        #     self.settings["use_initial_prompt"] = self._prompt_var.get()
        #     save_settings(self.settings)
        # tk.Checkbutton(vbar, textvariable=sv("chk_prompt_quick"),
        #                variable=self._prompt_var,
        #                command=_on_prompt_toggle,
        #                bg="#1a1a30", fg="#aaaacc", selectcolor="#1a1a30",
        #                activebackground="#1a1a30", activeforeground="#ccccee",
        #                font=("Yu Gothic", 9), cursor="hand2"
        #                ).pack(side=tk.LEFT, padx=(8, 0))
        # 音声再生: □自動再生 ▷（右詰め）← RIGHT を先にパックして領域を確保
        BG1 = "#1a1a30"
        self.play_btn = tk.Button(vbar, textvariable=sv("btn_play"),
                                  bg="#2a2a44", fg="#ccccee",
                                  font=("Yu Gothic", 9),
                                  relief=tk.FLAT, padx=8, cursor="hand2",
                                  command=self._toggle_play)
        self.play_btn.pack(side=tk.RIGHT, padx=(0, 10))
        tk.Checkbutton(vbar, textvariable=sv("chk_autoplay"),
                       variable=self.auto_play_var,
                       bg=BG1, fg="#aaaacc", selectcolor=BG1,
                       activebackground=BG1, activeforeground="#ccccee",
                       font=("Yu Gothic", 9)).pack(side=tk.RIGHT, padx=(0, 2))

        # 音声ステータス（RIGHT の後に LEFT でパック → 中間に収まる）
        self.voice_status = tk.StringVar(value="")
        self._anim_after_id = None
        self.voice_status_lbl = tk.Label(vbar, textvariable=self.voice_status,
                                         bg="#1a1a30", fg="#88cc88",
                                         font=("Yu Gothic", 9))
        self.voice_status_lbl.pack(side=tk.LEFT, padx=10)

        # 音声補助バー（2行目：ミュート）
        BG2 = "#161628"
        abar = tk.Frame(self.root, bg=BG2, pady=3)
        abar.pack(fill=tk.X)

        # ミュートチェック + スライダー（インスタンス変数で即時保存）
        self.mic_mute_var = tk.BooleanVar(value=self.settings.get("mic_mute_enabled", True))
        self.mic_vol_var  = tk.IntVar(value=self.settings.get("mic_mute_volume", 0))

        # チェック外し前のスライダー値を保存するクロージャ変数
        _saved_vol = [self.settings.get("mic_mute_volume", 0)]

        def _update_slider_state():
            state = tk.NORMAL if self.mic_mute_var.get() else tk.DISABLED
            mic_scale.configure(state=state)
            vol_icon_lbl.configure(state=state)
            vol_pct_lbl.configure(state=state)

        def _update_vol_display():
            v = self.mic_vol_var.get()
            vol_icon_lbl.configure(text="🔇" if v == 0 else "🔊")
            vol_pct_lbl.configure(text=f"{v}%")

        def _on_mute_toggle():
            """チェックボックス専用ハンドラ"""
            enabled = self.mic_mute_var.get()
            if not enabled:
                _saved_vol[0] = self.mic_vol_var.get()
                self.mic_vol_var.set(100)      # → _on_vol_change が発火するが問題なし
            else:
                self.mic_vol_var.set(_saved_vol[0])
            self.settings["mic_mute_enabled"] = enabled
            self.settings["mic_mute_volume"]  = self.mic_vol_var.get()
            save_settings(self.settings)
            _update_slider_state()

        def _on_vol_change(*_):
            """スライダー専用ハンドラ（表示更新＋保存のみ）"""
            self.settings["mic_mute_volume"] = self.mic_vol_var.get()
            save_settings(self.settings)
            _update_vol_display()

        tk.Checkbutton(abar, textvariable=sv("chk_input_mute"), variable=self.mic_mute_var,
                       command=_on_mute_toggle,
                       bg=BG2, fg="#aaaacc", selectcolor=BG2,
                       activebackground=BG2, activeforeground="#ccccee",
                       font=("Yu Gothic", 9), cursor="hand2"
                       ).pack(side=tk.LEFT, padx=(0, 2))

        mic_scale = tk.Scale(abar, variable=self.mic_vol_var,
                             from_=0, to=100, orient=tk.HORIZONTAL,
                             length=130, showvalue=False,
                             bg=BG2, fg="#aaaacc", troughcolor="#2a2a44",
                             highlightthickness=0, bd=0, cursor="hand2")
        mic_scale.pack(side=tk.LEFT, padx=2)

        vol_icon_lbl = tk.Label(abar, text="🔇", font=("Yu Gothic", 11),
                                bg=BG2, fg="#aaaacc")
        vol_icon_lbl.pack(side=tk.LEFT, padx=(2, 0))

        vol_pct_lbl = tk.Label(abar, text="0%", width=4,
                               bg=BG2, fg="#aaaacc", font=("Yu Gothic", 8))
        vol_pct_lbl.pack(side=tk.LEFT, padx=(2, 0))

        self.mic_vol_var.trace_add("write", _on_vol_change)
        _update_vol_display()
        _update_slider_state()

        # 送信ボタン＋自動送信チェック（右詰め）
        self.status = tk.StringVar(value=t("status_ready"))

        def _on_autosend_toggle():
            self.settings["auto_send"] = self.auto_send_var.get()
            save_settings(self.settings)

        # side=tk.RIGHT は後にpackしたものが右端になるため、
        # 送信ボタンを先に・チェックボックスを後にpackすると
        # 画面上は「□即送信  送信(右Ctrl)」の順になる
        tk.Button(abar, textvariable=sv("btn_send"),
                  command=self.send,
                  bg="#2244aa", fg="white",
                  font=("Yu Gothic", 10),
                  activebackground="#3355cc",
                  relief=tk.FLAT, padx=16, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=(0, 10))
        tk.Checkbutton(abar, textvariable=sv("chk_autosend"),
                       variable=self.auto_send_var,
                       command=_on_autosend_toggle,
                       bg=BG2, fg="#aaaacc", selectcolor=BG2,
                       activebackground=BG2, activeforeground="#ccccee",
                       font=("Yu Gothic", 9), cursor="hand2"
                       ).pack(side=tk.RIGHT, padx=(0, 2))

        # 下部ステータスバーを先に確保（expand=True の pane より前に pack しないと消える）
        bar = tk.Frame(self.root, bg="#1a1a30", pady=4)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(bar, textvariable=self.status,
                 bg="#1a1a30", fg="#9999bb",
                 font=("Yu Gothic", 10)).pack(side=tk.LEFT, padx=12)

        # テキストエリア＋ログを PanedWindow で分割
        pane = tk.PanedWindow(self.root, orient=tk.VERTICAL,
                              bg="#12121e", sashwidth=5, sashrelief=tk.FLAT)
        pane.pack(expand=True, fill=tk.BOTH, padx=8, pady=6)

        wrap = WRAP_MAP.get(self.settings["wrap_mode"], tk.CHAR)
        self.text = scrolledtext.ScrolledText(
            pane, wrap=wrap,
            font=("Yu Gothic", self.settings["font_size"]),
            bg=self.settings.get("editor_bg", "#0d0d1a"),
            fg=self.settings.get("editor_fg", "#c8c8e0"),
            insertbackground=self.settings.get("editor_cursor", "#8888ff"),
            padx=14, pady=12,
            relief=tk.FLAT, bd=0,
            undo=True, maxundo=200,
        )
        pane.add(self.text, minsize=120)
        self.text.focus_set()

        # ログエリア
        log_frame = tk.Frame(pane, bg="#0a0a18")
        pane.add(log_frame, minsize=160)

        # 📸 撮影先バー（テキストエリア下・レート制限上）
        self.shotbar = tk.Frame(log_frame, bg="#0f1a0f", pady=3)
        self.shotbar.pack(fill=tk.X)
        self._render_shot_buttons()

        # ── Claude.ai 使用状況バー（rate_limit_bridge.py 配置済み時のみ表示）──
        rbar = tk.Frame(log_frame, bg="#0c0c1c", pady=4)
        if self._rate_bridge_installed():
            rbar.pack(fill=tk.X)

        BAR_W, BAR_H = 160, 10
        BG_CLR = "#0c0c1c"

        def make_gauge(parent, title_key):
            """ラベル＋Canvasバー＋テキストのセットを作る。更新用関数を返す"""
            frm = tk.Frame(parent, bg=BG_CLR)
            frm.pack(side=tk.LEFT, padx=(14, 20))

            # 1行目：タイトル + リセット時間
            title_row = tk.Frame(frm, bg=BG_CLR)
            title_row.pack(anchor=tk.W)
            tk.Label(title_row, textvariable=sv(title_key), bg=BG_CLR, fg="#6677aa",
                     font=("Yu Gothic", 8)).pack(side=tk.LEFT)
            reset_lbl = tk.Label(title_row, text="", bg=BG_CLR, fg="#556688",
                                 font=("Yu Gothic", 8))
            reset_lbl.pack(side=tk.LEFT, padx=(6, 0))

            # 2行目：ゲージ + %
            row = tk.Frame(frm, bg=BG_CLR)
            row.pack(anchor=tk.W)

            cv = tk.Canvas(row, width=BAR_W, height=BAR_H,
                           bg="#1a1a2e", highlightthickness=0)
            cv.pack(side=tk.LEFT, padx=(0, 6))
            cv.create_rectangle(0, 0, BAR_W, BAR_H, fill="#1a1a2e", outline="")
            bar_rect = cv.create_rectangle(0, 0, 0, BAR_H, fill="#3366cc", outline="")

            pct_lbl = tk.Label(row, text="–", bg=BG_CLR, fg="#aaaacc",
                               font=("Yu Gothic", 8), width=5, anchor=tk.W)
            pct_lbl.pack(side=tk.LEFT)

            def update(pct, resets_ts, reset_done=False):
                if reset_done:
                    cv.itemconfig(bar_rect, fill="#44aa66")
                    cv.coords(bar_rect, 0, 0, 0, BAR_H)
                    pct_lbl.config(text="0%")
                    reset_lbl.config(text=t("rate_reset_done"))
                    return
                if pct is None:
                    return
                pct = min(max(float(pct), 0), 100)
                filled = int(BAR_W * pct / 100)
                color = "#44aa66" if pct < 70 else "#cc8800" if pct < 90 else "#cc3333"
                cv.itemconfig(bar_rect, fill=color)
                cv.coords(bar_rect, 0, 0, filled, BAR_H)
                pct_lbl.config(text=f"{pct:.0f}%")
                if resets_ts:
                    remain = int(resets_ts) - int(time.time())
                    if remain > 86400:
                        import datetime
                        dt = datetime.datetime.fromtimestamp(int(resets_ts))
                        days = t("rate_reset_days")
                        dow = days[dt.weekday()]
                        reset_lbl.config(text=t("rate_reset_at", hh=dt.hour, mm=dt.minute, dow=dow))
                    elif remain > 0:
                        h, m = divmod(remain // 60, 60)
                        reset_lbl.config(text=t("rate_reset", h=h, m=m))
                    else:
                        reset_lbl.config(text=t("rate_soon"))

            return update

        self._gauge_5h = make_gauge(rbar, "rate_5h")
        self._gauge_7d = make_gauge(rbar, "rate_7d")

        tk.Label(log_frame, textvariable=sv("lbl_log"), bg="#0a0a18", fg="#555566",
                 font=("Yu Gothic", 8), anchor=tk.W).pack(fill=tk.X, padx=4)
        self.log_box = tk.Text(
            log_frame, font=("Yu Gothic", 9),
            bg="#0a0a18", fg="#777788",
            relief=tk.FLAT, bd=0,
            height=3,
            state=tk.DISABLED,
        )
        self.log_box.pack(expand=True, fill=tk.BOTH, padx=4)

        # 言語切替コールバック登録
        _ready_texts = {LANG[l]["status_ready"] for l in LANG}
        def _on_lang_update():
            # ロックボタンのテキストを現在の言語に更新
            if self.locked:
                self.lock_btn.configure(text=t("btn_lock_fixed"))
            else:
                self.lock_btn.configure(text=t("btn_lock_select"))
            # status が「準備完了/Ready」相当なら再セット
            if self.status.get() in _ready_texts:
                self.status.set(t("status_ready"))
            # レート制限ラベルを即時更新（残り時間の言語を反映）
            self._poll_rate_limits()
        self._lang_cb = _on_lang_update
        _lang_callbacks.append(_on_lang_update)

        # キーバインド
        self.text.bind("<Control-s>", lambda e: self._save_to_file())
        self.text.bind("<Control-S>", lambda e: self._save_to_file())
        self.text.bind("<Control-o>", lambda e: self._load_from_file())
        self.text.bind("<Control-O>", lambda e: self._load_from_file())
        self.text.bind("<KeyPress-Control_R>", self._on_rctrl)
        self.root.bind("<KeyPress-Control_R>", self._on_rctrl)
        self.text.bind("<Control-z>", self._on_undo)
        self.text.bind("<Control-Z>", self._on_undo)
        self.text.bind("<Control-y>", self._on_redo)
        self.text.bind("<Control-Y>", self._on_redo)
        self.text.bind("<Up>",   lambda e: self._on_arrow("up"))
        self.text.bind("<Down>", lambda e: self._on_arrow("down"))
        self.root.bind("<KeyPress-F4>",   self._transcribe_start)
        self.root.bind("<KeyRelease-F4>", self._transcribe_stop)
        self.root.bind("<KeyPress-F5>",   self._instruct_start)
        self.root.bind("<KeyRelease-F5>", self._instruct_stop)

    # ── アンドゥ ─────────────────────────────────────────────
    def _on_undo(self, event=None):
        current = self.text.get("1.0", tk.END).strip()
        if current:
            # テキストあり → まずネイティブ undo を試みる
            native_ok = True
            try:
                self.text.edit_undo()
            except tk.TclError:
                native_ok = False   # undo スタックが空

            after = self.text.get("1.0", tk.END).strip()
            if not after or not native_ok:
                # undo で空になった、または undo できなかった → 履歴復元へ
                if self.sent_history:
                    self._restore_from_sent_history()
                elif not native_ok:
                    self._log(t("log_undo_limit"))
        else:
            # すでに空 → 履歴から復元
            if self.sent_history:
                self._restore_from_sent_history()
            else:
                self._log(t("log_undo_empty"))
        return "break"

    def _restore_from_sent_history(self):
        """sent_history の先頭を復元し、現在のテキストを redo スタックに積む"""
        current = self.text.get("1.0", tk.END).strip()
        self._redo_stack.append(current)   # "" も含めて積む（redo で元の位置に戻れるよう）
        restored = self.sent_history.pop(0)
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, restored)
        self.text.edit_reset()             # ネイティブ undo/redo をここでリセット
        remaining = len(self.sent_history)
        self._log(t("log_undo_restore", n=len(restored), remaining=remaining))
        self.status.set(t("status_restored", n=remaining))
        self.root.after(2000, lambda: self.status.set(t("status_ready")))

    def _on_redo(self, event=None):
        if self._redo_stack:
            # 現在のテキストを sent_history の先頭に戻す
            current = self.text.get("1.0", tk.END).strip()
            if current:
                self.sent_history.insert(0, current)
            # redo スタックから一つ取り出して表示
            restored = self._redo_stack.pop()
            self.text.delete("1.0", tk.END)
            self.text.insert(tk.END, restored)
            self.text.edit_reset()
            remaining = len(self._redo_stack)
            self._log(t("log_redo_advance", n=len(restored), remaining=remaining))
            label = t("status_redo", n=remaining) if remaining else t("status_redo_done")
            self.status.set(label)
            self.root.after(2000, lambda: self.status.set(t("status_ready")))
        else:
            self._log(t("log_redo_limit"))
        return "break"

    # ── チャンク分割ペースト ─────────────────────────────────
    def _paste_chunked(self, text):
        size  = self.settings.get("chunk_size", 50)
        delay = self.settings.get("chunk_delay", 0.15)
        chunks = [text[i:i+size] for i in range(0, len(text), size)]
        self._log(t("log_slow", n=len(chunks), size=size, delay=delay))
        for i, chunk in enumerate(chunks):
            pyperclip.copy(chunk)
            pyautogui.keyDown("ctrl")
            time.sleep(0.04)
            pyautogui.press("v")
            time.sleep(0.04)
            pyautogui.keyUp("ctrl")
            time.sleep(delay)

    # ── 音声入力 ─────────────────────────────────────────────
    def _load_model(self):
        """Whisperモデルを初回のみロード（別スレッドで実行）"""
        if _g.whisper_model is not None:
            return True
        self.root.after(0, lambda: self._start_status_anim(t("vstatus_loading_b")))
        devices = ("cuda", "cpu") if torch.cuda.is_available() else ("cpu",)
        for device in devices:
            try:
                self.root.after(0, lambda d=device: self._start_status_anim(
                    t("vstatus_loading_db", d=d)))
                self._log(t("log_voice_load", model=self.WHISPER_MODEL, device=device))
                model = whisper.load_model(self.WHISPER_MODEL, device=device)
                _g.whisper_model = model
                _g.whisper_device = device
                self.root.after(0, lambda: (self._stop_status_anim(),
                                            self.voice_status.set("")))
                self._log(t("log_voice_load_ok", device=device))
                return True
            except Exception as e:
                err = str(e)
                self._log(t("log_voice_load_fail", device=device, err=err))
                print(t("log_voice_load_fail", device=device, err=err))
                if device == "cpu":
                    self.root.after(0, lambda msg=err: messagebox.showerror(
                        t("dlg_whisper_err_t"), t("dlg_whisper_err", msg=msg)))
                    self.root.after(0, lambda: (self._stop_status_anim(),
                                                self.voice_status.set(t("vstatus_fail"))))
        return False

    @staticmethod
    def _with_audio_endpoint(fn):
        """COM IAudioEndpointVolume を取得し fn(vol) を呼び出す。
        初回のみ CoInitialize + Activate を行い、以降は _g にキャッシュされた
        interface を再利用することで繰り返し CoInitialize による vtable 破壊を防ぐ。"""
        if _g.audio_vol is None:
            import comtypes
            comtypes.CoInitialize()          # メインスレッドで1回だけ呼ぶ
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            speakers = AudioUtilities.GetSpeakers()
            dev = speakers._dev if hasattr(speakers, "_dev") else speakers
            _g.audio_iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            _g.audio_vol   = cast(_g.audio_iface, POINTER(IAudioEndpointVolume))
        return fn(_g.audio_vol)

    def _mute_system_audio(self):
        """録音開始前にシステム音をミュート/低減（設定に従う）"""
        if not self.settings.get("mic_mute_enabled", True):
            return
        try:
            def do_mute(vol):
                current_vol  = vol.GetMasterVolumeLevelScalar()
                _g.prev_audio = {
                    "mute": vol.GetMute(),
                    "vol":  current_vol,
                }
                ratio  = self.settings.get("mic_mute_volume", 0) / 100.0
                target = current_vol * ratio   # 現在値に対する相対割合
                if ratio == 0.0:
                    vol.SetMute(1, None)
                else:
                    vol.SetMute(0, None)
                    vol.SetMasterVolumeLevelScalar(target, None)
                self._log(t("log_mic_mute", cur=int(current_vol*100), tgt=int(target*100), ratio=int(ratio*100)))
            self._with_audio_endpoint(do_mute)
        except Exception as e:
            self._log(t("log_mic_mute_fail", e=e))
            _g.prev_audio = None

    def _restore_system_audio(self):
        """録音終了後にシステム音を録音前の状態に戻す"""
        if _g.prev_audio is None:
            return
        try:
            def do_restore(vol):
                vol.SetMasterVolumeLevelScalar(_g.prev_audio["vol"], None)
                vol.SetMute(_g.prev_audio["mute"], None)
                self._log(t("log_mic_restore"))
            self._with_audio_endpoint(do_restore)
        except Exception as e:
            self._log(t("log_mic_rest_fail", e=e))
        finally:
            _g.prev_audio = None

    def _start_recording(self):
        if _g.recording_owner is not None and _g.recording_owner is not self:
            self.voice_status.set(t("vstatus_other_rec"))
            return
        _g.recording_owner = self
        self._mute_system_audio()
        if getattr(self, "_playing", False):
            self._stop_audio()
        # initial_prompt をメインスレッドで安全にキャプチャ（スレッドセーフ）
        use_ctx  = self.settings.get("use_user_context",   True)
        use_hist = self.settings.get("use_initial_prompt", False)
        if use_ctx:
            self._reload_user_ctx_if_changed()
        if use_ctx or use_hist:
            user_ctx = self._user_ctx_content if use_ctx else ""
            history  = self._build_initial_prompt() or "" if use_hist else ""
            current  = self.text.get("1.0", tk.END).strip() if use_hist else ""
            combined = "\n".join(filter(None, [user_ctx, history, current]))
            self._initial_prompt_cache = combined[-500:] if combined else None
            # DEBUG: 録音開始時に内容をファイルに保存（リリース時は非表示）
            # try:
            #     with open(self._PROMPT_PATH, "w", encoding="utf-8") as _f:
            #         _f.write(self._initial_prompt_cache or "(None)")
            # except Exception:
            #     pass
        else:
            self._initial_prompt_cache = None
        self.audio_frames = []
        self.recording    = True
        if self.voice_mode == "input":
            self.root.after(0, lambda: self.voice_status.set(t("vstatus_rec")))
            self.root.after(0, lambda: self.transcribe_btn.configure(bg="#aa2222", fg="white"))
        else:
            self.root.after(0, lambda: self.voice_status.set(t("vstatus_inst_rec")))
            self.root.after(0, lambda: self.instruct_btn.configure(bg="#aa6600", fg="white"))
        threading.Thread(target=self._record_thread, daemon=True).start()

    def _record_thread(self):
        frames = []
        def callback(indata, frame_count, time_info, status):
            if self.recording:
                frames.append(indata.copy())
        with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1,
                             dtype="float32", callback=callback):
            while self.recording:
                time.sleep(0.05)
        # recording停止後にフレームを確定してから転写を起動
        self.audio_frames = frames
        self.root.after(0, lambda: self.voice_status.set(t("vstatus_trans")))
        threading.Thread(target=self._transcribe_thread, daemon=True).start()

    def _stop_recording_and_transcribe(self):
        self.recording = False
        self._restore_system_audio()
        self.root.after(0, lambda: self.transcribe_btn.configure(bg="#2a2a44", fg="#ccccee"))
        self.root.after(0, lambda: self.instruct_btn.configure(bg="#2a2a44", fg="#ccccee"))
        # 転写スレッドはここでは起動しない → _record_thread の末尾で起動

    def _transcribe_thread(self):
        _g.last_record_window = self
        _g.recording_owner    = None  # 転写開始 = 録音完了
        self._log(t("log_voice_trans_start", n=len(self.audio_frames)))
        if not self.audio_frames:
            self._log(t("log_voice_no_audio"))
            self.root.after(0, lambda: self.voice_status.set(t("vstatus_silent")))
            return
        try:
            audio = np.concatenate(self.audio_frames, axis=0).flatten()
            self.last_audio = audio   # 再生ボタン用に保存
            self._log(t("log_voice_audio_len", sec=len(audio)/self.SAMPLE_RATE))
        except Exception as e:
            self._log(t("log_voice_audio_err", e=e))
            return
        if len(audio) < self.SAMPLE_RATE * 0.5:
            self._log(f"[VOICE] 音声が短すぎるためスキップ ({len(audio)/self.SAMPLE_RATE:.2f}s)")
            self.root.after(0, lambda: self.voice_status.set(t("vstatus_silent")))
            return
        if not self._load_model():
            return
        with _g.transcribe_lock:
            try:
                fp16 = (_g.whisper_device == "cuda")
                # テキストエリアの内容を文脈ヒントとして渡す（同音異義語の精度向上）
                initial_prompt = getattr(self, "_initial_prompt_cache", None)
                self._log(t("log_voice_transcribe", fp16=fp16,
                             prompt=t("log_yes") if initial_prompt else t("log_no")))
                wlang = self.settings.get("whisper_language", "ja")
                common_args = dict(
                    language=None if wlang == "auto" else wlang,
                    initial_prompt=initial_prompt,
                )
                try:
                    result = _g.whisper_model.transcribe(audio, fp16=fp16, **common_args)
                    # fp16でNaNが発生した場合はfp32でリトライ
                    import math
                    segs = result.get("segments", [])
                    if fp16 and segs and any(
                        math.isnan(s.get("avg_logprob", 0)) for s in segs
                    ):
                        raise ValueError("nan in logprob")
                except Exception as _fp16_err:
                    _is_nan_err = ("nan" in str(_fp16_err).lower() or
                                   "logits" in str(_fp16_err).lower() or
                                   "key.size" in str(_fp16_err).lower() or
                                   "nan in logprob" in str(_fp16_err))
                    if fp16 and _is_nan_err:
                        self._log(t("log_fp16_retry"))
                        try:
                            result = _g.whisper_model.transcribe(audio, fp16=False, **common_args)
                        except Exception as _fp32_err:
                            if _is_nan_err or "nan" in str(_fp32_err).lower():
                                # fp32でもNaN → モデルが破損、再ロードして回復
                                self._log(t("log_fp32_reload"))
                                _g.whisper_model = None
                                if torch.cuda.is_available():
                                    torch.cuda.empty_cache()
                                self._load_model()
                                result = _g.whisper_model.transcribe(audio, fp16=False, **common_args)
                            else:
                                raise
                    else:
                        raise
                text = result["text"].strip()

                # 無音検出: セグメントの no_speech_prob が高い場合は破棄
                segments = result.get("segments", [])
                if segments:
                    avg_no_speech = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
                    if avg_no_speech > 0.6:
                        self._log(t("log_voice_silent_skip", prob=avg_no_speech))
                        self.root.after(0, lambda: self.voice_status.set(""))
                        return

                # ハルシネーション文字列フィルタ（無音時にWhisperが幻覚的に出力する既知フレーズ）
                HALLUCINATIONS = {
                    "ご視聴ありがとうございました",
                    "字幕は自動生成されています",
                    "チャンネル登録をお願いします",
                    "ありがとうございました",
                    "よろしくお願いします",
                    "お疲れ様でした",
                    "次回は、次回の動画でお会いしましょう。",
                    "ご視聴ありがとうございました。",
                }
                if text in HALLUCINATIONS or (not text):
                    self._log(t("log_voice_halluc", text=repr(text)))
                    self.root.after(0, lambda: self.voice_status.set(""))
                    return

                short = text[:60] + ("…" if len(text) > 60 else "")
                self._log(t("log_voice_result", text=short))
                self.root.after(0, lambda: self._insert_voice_text(text))
            except Exception as e:
                self._log(t("log_voice_err", e=e))
                self.root.after(0, lambda msg=str(e): self.voice_status.set(t("vstatus_err", e=msg)))
                # 転写例外はモデルの内部状態を壊す可能性があるため再ロードして回復
                _g.whisper_model = None
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                self._load_model()

    def _insert_voice_text(self, text):
        if self.voice_mode == "edit":
            self.voice_mode = "input"
            self.voice_status.set(t("vstatus_editing"))
            # base_text をメインスレッドで安全に取得してからスレッドへ渡す
            base_text = self.text.get("1.0", tk.END).strip()
            threading.Thread(target=self._run_edit,
                             args=(text, base_text), daemon=True).start()
        else:
            if text:
                try:
                    # 選択範囲があれば置き換え、なければカーソル位置に挿入
                    self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                except tk.TclError:
                    pass  # 選択なし
                self.text.insert(tk.INSERT, text)
            self.voice_status.set(t("vstatus_done"))
            self.root.after(2000, lambda: self.voice_status.set(""))
            self.text.focus_set()
            if self.auto_play_var.get():
                self._play_audio()
            if self.auto_send_var.get():
                self.root.after(200, self.send)   # 文字起こし後に自動送信
        self._schedule_offload()

    # ── 音声編集（指示モード） ───────────────────────────────
    def _run_edit(self, instruction, base_text=""):
        if not base_text:
            self.root.after(0, lambda: messagebox.showwarning(
                t("dlg_no_base_t"), t("dlg_no_base")))
            self.root.after(0, lambda: self.voice_status.set(""))
            return

        # 編集前テキストを履歴に保存 → Ctrl+Z で編集前に戻れる
        self.sent_history.insert(0, base_text)
        if len(self.sent_history) > self.HISTORY_MAX:
            self.sent_history.pop()

        self._log(t("log_edit_inst", text=instruction[:50]))
        backend = self.settings.get("edit_backend", "claude_cli")

        try:
            if backend == "claude_cli":
                result = self._edit_via_claude_cli(base_text, instruction)
            elif backend == "ollama":
                result = self._edit_via_ollama(base_text, instruction)
            elif backend == "gemini":
                result = self._edit_via_gemini(base_text, instruction)
            else:
                result = None

            if result:
                short = result[:60] + ("…" if len(result) > 60 else "")
                self._log(t("log_edit_done", text=short))
                self.root.after(0, lambda: self._apply_edit_result(result))
            else:
                self.root.after(0, lambda: self.voice_status.set(t("vstatus_edit_fail")))
        except Exception as e:
            full_msg = str(e)
            self._log(t("log_edit_err", e=full_msg))
            short_msg = full_msg.splitlines()[0][:80]  # 先頭1行・80文字でステータス表示
            self.root.after(0, lambda msg=short_msg: self.voice_status.set(t("vstatus_edit_err", msg=msg)))

    def _build_prompt(self, base_text, instruction):
        return (
            "以下の元テキストに対して、指示に従って修正してください。\n"
            "修正後のテキストのみを返し、説明や前置きは不要です。\n\n"
            f"【元テキスト】\n{base_text}\n\n"
            f"【指示】\n{instruction}"
        )

    def _find_claude_exe(self):
        """claude コマンドのフルパスを検索する"""
        import shutil
        # PATH から検索
        found = shutil.which("claude")
        if found:
            return found
        # npm グローバルインストールの一般的な場所
        candidates = [
            os.path.expandvars(r"%APPDATA%\npm\claude.cmd"),
            os.path.expandvars(r"%APPDATA%\npm\claude"),
            os.path.expandvars(r"%LOCALAPPDATA%\npm\claude.cmd"),
            os.path.expandvars(r"%LOCALAPPDATA%\npm\claude"),
            os.path.expandvars(r"%ProgramFiles%\nodejs\claude.cmd"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    @staticmethod
    def _find_node_cli(claude_cmd_path):
        """claude.CMD の場所から node.exe と cli.js のパスを返す。
        cmd.exe をバイパスするために node を直接呼ぶ用途。"""
        import shutil
        if claude_cmd_path is None:
            return None, None
        # claude.CMD と同階層の node_modules を探す
        npm_dir = os.path.dirname(os.path.abspath(claude_cmd_path))
        cli_js  = os.path.join(npm_dir, "node_modules", "@anthropic-ai", "claude-code", "cli.js")
        if not os.path.exists(cli_js):
            return None, None
        # node.exe: npm フォルダにバンドルされていれば使い、なければ PATH から探す
        node_exe = os.path.join(npm_dir, "node.exe")
        if not os.path.exists(node_exe):
            node_exe = shutil.which("node") or "node"
        return node_exe, cli_js

    def _edit_via_claude_cli(self, base_text, instruction):
        prompt = self._build_prompt(base_text, instruction)
        claude_exe = self._find_claude_exe()
        if not claude_exe:
            raise RuntimeError(t("err_no_claude"))
        # claude.CMD は cmd.exe 経由で引数を渡すため、改行を含む引数が途中で切れる。
        # node.js を直接呼び出すことで cmd.exe をバイパスし、改行を含むプロンプトを
        # 確実に渡せる。
        node_exe, cli_js = self._find_node_cli(claude_exe)
        if node_exe is None:
            raise RuntimeError(t("err_no_claude"))
        self._log(t("log_edit_cli", path=cli_js))
        self._log(t("log_edit_prompt", text=prompt[:80]))
        r = subprocess.run(
            [node_exe, cli_js, "--dangerously-skip-permissions", "--print", prompt],
            capture_output=True, timeout=120,
        )
        self._log(f"[EDIT] rc={r.returncode} stdout={len(r.stdout)}bytes")
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace").strip() or f"claude CLI エラー (rc={r.returncode})"
            raise RuntimeError(err)
        out = r.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            detail = r.stderr.decode("utf-8", errors="replace").strip()[:200] or "(出力なし・要ログイン確認: claude auth login)"
            self._log(t("log_edit_empty", detail=detail))
        return out

    def _edit_via_ollama(self, base_text, instruction):
        import urllib.request, urllib.error, json as _json
        prompt = self._build_prompt(base_text, instruction)
        host  = self.settings.get("ollama_host", "http://localhost:11434")
        model = self.settings.get("ollama_model", "qwen3:8b")

        # ① Ollama が起動しているか確認（/api/tags に軽くアクセス）
        try:
            with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
                tags_data = _json.loads(r.read())
        except urllib.error.URLError:
            raise RuntimeError(t("err_ollama_down"))

        # ② モデルがダウンロード済みか確認
        available = [m.get("name", "") for m in tags_data.get("models", [])]
        # "qwen3:8b" は "qwen3:8b" または "qwen3:latest" 等で一致を確認
        model_ok = any(m == model or m.startswith(model.split(":")[0] + ":")
                       for m in available)
        if not model_ok:
            raise RuntimeError(t("err_ollama_model", model=model))

        self._log(t("log_edit_ollama", model=model))
        body = _json.dumps({
            "model": model, "prompt": prompt, "stream": False
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                data = _json.loads(res.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(t("err_ollama_other", msg=f"HTTP {e.code}: {e.reason}"))
        return data.get("response", "").strip()

    def _edit_via_gemini(self, base_text, instruction):
        try:
            from google import genai
        except ImportError:
            raise RuntimeError(t("err_no_gemini_pkg"))
        key = self.settings.get("gemini_api_key", "")
        if not key:
            raise RuntimeError(t("err_no_api_key"))
        model = self.settings.get("gemini_model", "gemini-2.0-flash")
        self._log(t("log_edit_gemini", model=model))
        client = genai.Client(api_key=key)
        response = client.models.generate_content(model=model,
                                                  contents=self._build_prompt(base_text, instruction))
        try:
            result = response.text.strip()
        except Exception as e:
            raise RuntimeError(f"Gemini response error: {e}")
        if not result:
            raise RuntimeError("Gemini returned empty response")
        return result

    def _apply_edit_result(self, corrected):
        # HTML検出：先頭がHTMLタグっぽければブラウザプレビューを提案
        if self._looks_like_html(corrected):
            open_browser = messagebox.askyesno(
                t("html_detected"), t("html_ask"), parent=self.root)
            if open_browser:
                self._preview_html(corrected)
                self.voice_status.set(t("vstatus_edit_done"))
                self.root.after(2000, lambda: self.voice_status.set(""))
                return
        self._redo_stack.clear()            # 新たな編集で既存のredo履歴をクリア
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, corrected)
        self.text.edit_reset()              # ネイティブundoをリセット → Ctrl-Z で確実に履歴ベースundoへ
        self.voice_status.set(t("vstatus_edit_done"))
        self.root.after(2000, lambda: self.voice_status.set(""))
        self.text.focus_set()
        if self.auto_play_var.get():
            self._play_audio()

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        s = text.lstrip()
        return s.startswith(("<!DOCTYPE", "<!doctype", "<html", "<HTML"))

    def _preview_html(self, html: str):
        import webbrowser, tempfile, pathlib
        tmp = pathlib.Path(tempfile.mktemp(suffix=".html"))
        tmp.write_text(html, encoding="utf-8")
        self._log(t("log_html_preview", path=str(tmp)))
        webbrowser.open(tmp.as_uri())

    # ── 文字起こし／指令 PTTボタン ───────────────────────────
    def _transcribe_start(self, event=None):
        self._ptt_held = True
        if not self.recording:
            self.voice_mode = "input"
            threading.Thread(target=self._ensure_model_then_record,
                             args=("input",), daemon=True).start()

    def _transcribe_stop(self, event=None):
        self._ptt_held = False
        if self.recording and self.voice_mode == "input":
            self._stop_recording_and_transcribe()

    def _instruct_start(self, event=None):
        self._instr_held = True
        if not self.recording:
            self.voice_mode = "edit"
            self._log(t("log_edit_mode"))
            threading.Thread(target=self._ensure_model_then_record,
                             args=("edit",), daemon=True).start()

    def _instruct_stop(self, event=None):
        self._instr_held = False
        if self.recording and self.voice_mode == "edit":
            self._stop_recording_and_transcribe()

    # ── 音声再生 ─────────────────────────────────────────────
    def _toggle_play(self):
        if getattr(self, "_playing", False):
            self._stop_audio()
        else:
            self._play_audio()

    def _play_audio(self):
        if self.last_audio is None:
            return
        sd.stop()
        self._playing = True
        self.play_btn.configure(text=t("btn_play_stop"))
        self.voice_status.set(t("vstatus_playing"))
        sd.play(self.last_audio, self.SAMPLE_RATE)
        threading.Thread(target=self._wait_play_end, daemon=True).start()

    def _wait_play_end(self):
        sd.wait()
        self.root.after(0, self._on_play_finished)

    def _on_play_finished(self):
        self._playing = False
        self.play_btn.configure(text=t("btn_play"))
        if self.voice_status.get() == t("vstatus_playing"):
            self.voice_status.set("")

    def _stop_audio(self):
        sd.stop()
        self._on_play_finished()

    def _schedule_offload(self):
        """設定に従ってモデルのオフロードを予約"""
        if _g.whisper_model is None:
            return
        mode = self.settings.get("model_mode", "gpu")
        if mode == "gpu":
            return
        if not torch.cuda.is_available():
            return
        # 既存のタイマーをキャンセル
        if _g.offload_timer is not None:
            _splash_root.after_cancel(_g.offload_timer)
            _g.offload_timer = None
        delay_min = self.settings.get("cpu_offload_delay", 1) if mode == "cpu" else 0
        delay_ms  = int(delay_min * 60 * 1000)
        _g.offload_timer = _splash_root.after(delay_ms, self._do_offload)
        delay_label = t({0: "delay_0", 1: "delay_1", 5: "delay_5", 10: "delay_10"}.get(delay_min, "delay_1"))
        self._log(t("log_gpu_sched", mode=mode, label=delay_label))

    def _do_offload(self):
        """モデルをGPUからオフロード（別スレッドで実行）"""
        _g.offload_timer = None
        threading.Thread(target=self._offload_thread, daemon=True).start()

    def _offload_thread(self):
        mode = self.settings.get("model_mode", "gpu")
        if _g.whisper_model is None:
            return
        # UI更新は最後に録音したウィンドウに対して行う
        ui_win = _g.last_record_window or self
        try:
            if mode == "cpu":
                _g.whisper_model = _g.whisper_model.to("cpu")
                _g.whisper_device = "cpu"
                torch.cuda.empty_cache()
                self._log(t("log_gpu_to_cpu"))
                ui_win.root.after(0, lambda: ui_win.voice_status.set(t("vstatus_cpu_wait")))
                ui_win.root.after(3000, lambda: ui_win.voice_status.set(""))

        except Exception as e:
            self._log(t("log_gpu_err", e=e))

    def _ensure_model_then_record(self, mode: str):
        """モデルをロード／GPU復帰してから録音開始。mode="input"|"edit" """
        # オフロード予約をキャンセル
        if _g.offload_timer is not None:
            _splash_root.after_cancel(_g.offload_timer)
            _g.offload_timer = None
        if _g.whisper_model is None:
            self._load_model()
        elif (self.settings.get("model_mode") != "unload"
              and _g.whisper_device == "cpu"
              and torch.cuda.is_available()):
            # CPU待機中 → GPUへ戻す（二重実行防止）
            if _g.gpu_restoring:
                self._log(t("log_gpu_skip"))
                held = self._ptt_held if mode == "input" else self._instr_held
                if held:
                    self.root.after(0, self._start_recording)
                return
            _g.gpu_restoring = True
            self.root.after(0, lambda: self._start_status_anim(t("vstatus_gpu_back_b")))
            try:
                _g.whisper_model  = _g.whisper_model.to("cuda")
                _g.whisper_device = "cuda"
                self._log(t("log_gpu_restore"))
            except Exception as e:
                self._log(t("log_gpu_restore_fail", e=e))
            finally:
                _g.gpu_restoring = False
            self.root.after(0, self._stop_status_anim)
        # モデル準備完了後、ボタンがまだ押されているときだけ録音開始
        held = self._ptt_held if mode == "input" else self._instr_held
        if held:
            self.root.after(0, self._start_recording)
        else:
            # モデルロード中にボタンが離された → 録音せず準備完了を表示
            self.root.after(0, lambda: self.voice_status.set(""))

    # ── 矢印キー ─────────────────────────────────────────────
    def _on_arrow(self, key):
        """テキストが空なら矢印キーをターゲットへ送信、あれば通常カーソル移動"""
        if not self.text.get("1.0", tk.END).strip():
            if not self.target_hwnd:
                return "break"
            activate_hwnd(self.target_hwnd)
            # sleep を使わず after() でイベントループを止めない
            self.root.after(180, lambda: self._send_arrow_key(key))
            return "break"
        # テキストあり → 通常のカーソル移動

    def _send_arrow_key(self, key):
        pyautogui.press(key)
        self._log(t("log_key_send", key=key))
        # テキストウィジェット自体にフォーカスを戻す
        self.root.after(100, self.text.focus_set)

    # ── ロック／解除トグル ───────────────────────────────────
    def _toggle_topmost(self):
        val = self._topmost_var.get()
        self.root.attributes("-topmost", val)
        self.settings["topmost"] = val
        save_settings(self.settings)

    def _toggle_lock(self):
        self.locked = not self.locked
        if self.locked:
            self.lock_btn.configure(text=t("btn_lock_fixed"), bg="#331a1a", fg="#ff6666")
            self._log(t("log_lock_on"))
        else:
            self.lock_btn.configure(text=t("btn_lock_select"), bg="#334466", fg="#aaccff")
            self._log(t("log_lock_off"))
            self._poll_foreground()

    def _poll_foreground(self):
        """解除中: フォアグラウンドウィンドウが変わったら送信先に自動設定（ロックしない）"""
        if self.locked:
            return
        if self._shot_picking or __import__("time").time() < self._shot_suppress_until:
            self.root.after(150, self._poll_foreground)
            return
        hwnd = user32.GetForegroundWindow()
        if self._screenshot_target and hwnd == self._screenshot_target[0]:
            self.root.after(150, self._poll_foreground)
            return
        our_hwnds  = {w.root.winfo_id() for w in _g.windows}
        our_titles = {w.root.title()     for w in _g.windows}
        # システムプロセス・CLI 実行時の一時ウィンドウを除外
        _IGNORE_TITLES = {"", "C:\\Windows\\system32\\cmd.exe"}
        _IGNORE_PATHS  = ("\\system32\\", "\\SysWOW64\\", "\\WindowsApps\\")
        if hwnd and hwnd not in our_hwnds:
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
                ignore = (title in _IGNORE_TITLES or
                          any(p.lower() in title.lower() for p in _IGNORE_PATHS))
                if not ignore and title not in our_titles and hwnd != self.target_hwnd:
                    self._register_target(hwnd, title)
                    self._log(t("log_auto_target", title=title))
        # 解除中は常に監視を継続
        self.root.after(150, self._poll_foreground)

    # ── 送信先ボタン描画 ─────────────────────────────────────
    def _register_target(self, hwnd, title):
        """送信先を設定"""
        self.target_hwnd  = hwnd
        self.target_title = title
        self.target_var.set(title[:39] + "…" if len(title) > 39 else title)

    # ── ログ ─────────────────────────────────────────────────
    def _log(self, msg):
        """スレッドセーフなログ出力（メイン/バックグラウンドスレッド両対応）"""
        def _do(m=msg):
            self.log_box.configure(state=tk.NORMAL)
            self.log_box.insert(tk.END, m + "\n")
            self.log_box.see(tk.END)
            self.log_box.configure(state=tk.DISABLED)
        self.root.after(0, _do)

    # ── レート制限ポーリング ──────────────────────────────────
    RATE_POLL_MS = 60_000  # 1分ごとに再読み込み

    @staticmethod
    def _find_rate_cache():
        """rate_limits_cache.json を探す（~/.zikkokey/ 優先、次にスクリプトと同フォルダ）"""
        candidates = [
            os.path.join(os.path.expanduser("~"), ".zikkokey", "rate_limits_cache.json"),
            os.path.join(SCRIPT_DIR, "rate_limits_cache.json"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    @staticmethod
    def _rate_bridge_installed():
        """rate_limit_bridge.py が配置済みかどうかを返す"""
        candidates = [
            os.path.join(os.path.expanduser("~"), ".zikkokey", "rate_limit_bridge.py"),
            os.path.join(SCRIPT_DIR, "rate_limit_bridge.py"),
        ]
        return any(os.path.exists(p) for p in candidates)

    def _poll_rate_limits(self):
        """rate_limits_cache.json を読んでゲージを更新、1分ごとに繰り返す"""
        try:
            cache_path = self._find_rate_cache()
            if cache_path:
                with open(cache_path, encoding="utf-8") as f:
                    data = json.load(f)
                rl = data.get("rate_limits", {})
                now = int(time.time())
                for gauge_fn, key in ((self._gauge_5h, "five_hour"),
                                      (self._gauge_7d, "seven_day")):
                    entry    = rl.get(key, {})
                    pct      = entry.get("used_percentage")
                    resets_at = entry.get("resets_at")
                    if resets_at and int(resets_at) <= now:
                        # リセット時刻を過ぎている → 0%・リセット済み表示
                        gauge_fn(None, None, reset_done=True)
                    else:
                        gauge_fn(pct, resets_at)
                        # リセット時刻ちょうどに追加ポーリングをスケジュール
                        if resets_at:
                            remain_ms = (int(resets_at) - now) * 1000
                            if 0 < remain_ms < self.RATE_POLL_MS:
                                self.root.after(remain_ms + 500,
                                                self._poll_rate_limits)
        except Exception:
            pass
        self.root.after(self.RATE_POLL_MS, self._poll_rate_limits)

    # ── ファイル読み込み ──────────────────────────────────────
    def _load_from_file(self):
        from tkinter import filedialog
        self._save_ime_font()   # ダイアログ前に Tk の IME フォントを保存
        path = filedialog.askopenfilename(
            parent=self.root,
            title=t("dlg_load_title"),
            filetypes=[("テキストファイル", "*.txt"), ("すべてのファイル", "*.*")],
        )
        if not path:
            return
        for enc in ("utf-8-sig", "utf-8", "cp932"):
            try:
                with open(path, "r", encoding=enc) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                continue
        else:
            self._log(t("log_load_fail_enc", path=path))
            return
        # 現在の内容を履歴に保存してから置換
        current = self.text.get("1.0", tk.END).strip()
        if current:
            self.sent_history.insert(0, current)
            if len(self.sent_history) > self.HISTORY_MAX:
                self.sent_history.pop()
        self._redo_stack.clear()
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, content.rstrip("\n"))
        self.text.edit_reset()
        short = os.path.basename(path)
        self.status.set(t("status_loaded", path=short))
        self._log(f"[LOAD] {path}")
        self.root.after(3000, lambda: self.status.set(t("status_ready")))
        # Win32 ファイルダイアログはネイティブ HWND を使うため、閉じた後に
        # Tk が ImmSetCompositionFont を呼び直さず IME フォントが壊れる。
        # ダイアログが完全に閉じてから IMM32 API で直接再設定する。
        self.root.after(100, self._reset_ime_font)

    # IME フォント保存・復元用
    _LOGFONTW = None   # クラス共有の LOGFONTW 型（遅延生成）
    _saved_ime_lf = None

    @staticmethod
    def _make_logfontw():
        import ctypes
        class LOGFONTW(ctypes.Structure):
            _fields_ = [
                ("lfHeight",         ctypes.c_long),
                ("lfWidth",          ctypes.c_long),
                ("lfEscapement",     ctypes.c_long),
                ("lfOrientation",    ctypes.c_long),
                ("lfWeight",         ctypes.c_long),
                ("lfItalic",         ctypes.c_byte),
                ("lfUnderline",      ctypes.c_byte),
                ("lfStrikeOut",      ctypes.c_byte),
                ("lfCharSet",        ctypes.c_byte),
                ("lfOutPrecision",   ctypes.c_byte),
                ("lfClipPrecision",  ctypes.c_byte),
                ("lfQuality",        ctypes.c_byte),
                ("lfPitchAndFamily", ctypes.c_byte),
                ("lfFaceName",       ctypes.c_wchar * 32),
            ]
        return LOGFONTW

    def _save_ime_font(self):
        """ダイアログを開く直前に Tk が設定した IME フォントを保存する"""
        try:
            import ctypes
            LOGFONTW = self._make_logfontw()
            hwnd = self.text.winfo_id()
            himc = ctypes.windll.imm32.ImmGetContext(hwnd)
            if not himc:
                return
            try:
                lf = LOGFONTW()
                if ctypes.windll.imm32.ImmGetCompositionFontW(himc, ctypes.byref(lf)):
                    InputWindow._saved_ime_lf = lf
            finally:
                ctypes.windll.imm32.ImmReleaseContext(hwnd, himc)
        except Exception:
            pass

    # ── ステータスアニメーション ──────────────────────────────
    _ANIM_DOTS = ["", "･", "･･", "･･･"]

    def _start_status_anim(self, base_text: str):
        """赤色でドットアニメーション付きステータスを表示する。
        base_text の末尾に ･ ･･ ･･･ をループしながら付加する。"""
        self._stop_status_anim()
        self.voice_status_lbl.configure(fg="#ff6666")
        step = [0]

        def _tick():
            self.voice_status.set(base_text + self._ANIM_DOTS[step[0] % 4])
            step[0] += 1
            self._anim_after_id = self.root.after(500, _tick)

        _tick()

    def _stop_status_anim(self):
        """アニメーション停止・ラベル色をデフォルト（緑）に戻す。"""
        if self._anim_after_id is not None:
            self.root.after_cancel(self._anim_after_id)
            self._anim_after_id = None
        self.voice_status_lbl.configure(fg="#88cc88")

    def _reset_ime_font(self):
        """ダイアログを閉じた後に保存済み IME フォントを復元する"""
        try:
            import ctypes
            lf = InputWindow._saved_ime_lf
            if lf is None:
                return
            hwnd = self.text.winfo_id()
            self.text.focus_set()
            himc = ctypes.windll.imm32.ImmGetContext(hwnd)
            if not himc:
                return
            try:
                ctypes.windll.imm32.ImmSetCompositionFontW(himc, ctypes.byref(lf))
            finally:
                ctypes.windll.imm32.ImmReleaseContext(hwnd, himc)
        except Exception:
            pass

    # ── ファイル保存 ──────────────────────────────────────────
    def _save_to_file(self):
        from tkinter import filedialog
        content = self.text.get("1.0", tk.END)
        if not content.strip():
            return
        self._save_ime_font()   # ダイアログ前に Tk の IME フォントを保存
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title=t("dlg_save_title"),
            defaultextension=".txt",
            filetypes=[("テキストファイル", "*.txt"), ("すべてのファイル", "*.*")],
        )
        if not path:
            self.status.set(t("status_save_cancel"))
            self.root.after(2000, lambda: self.status.set(t("status_ready")))
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        short = os.path.basename(path)
        self.status.set(t("status_saved", path=short))
        self._log(f"[SAVE] {path}")
        self.root.after(3000, lambda: self.status.set(t("status_ready")))
        self.root.after(100, self._reset_ime_font)

    # ── 設定ダイアログ ───────────────────────────────────────
    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title(t("dlg_settings"))
        dlg.geometry("500x700")
        dlg.resizable(False, True)
        dlg.grab_set()
        dlg.attributes("-topmost", True)

        # スクロール可能なキャンバス
        canvas = tk.Canvas(dlg, highlightthickness=0)
        sb = tk.Scrollbar(dlg, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = tk.Frame(canvas)
        cwin = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _on_inner_cfg(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_cfg(e):
            canvas.itemconfig(cwin, width=e.width)
        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        inner.bind("<Configure>", _on_inner_cfg)
        canvas.bind("<Configure>", _on_canvas_cfg)
        canvas.bind("<MouseWheel>", _on_wheel)
        dlg.bind_all("<MouseWheel>", _on_wheel)
        dlg.bind("<Destroy>", lambda e: dlg.unbind_all("<MouseWheel>") if e.widget is dlg else None)

        pad = {"padx": 20, "pady": 6}

        def section(text):
            tk.Label(inner, text=text, font=("Yu Gothic", 10, "bold"),
                     anchor=tk.W).pack(fill=tk.X, **pad)

        def separator():
            tk.Frame(inner, bg="#cccccc", height=1).pack(fill=tk.X, padx=20, pady=4)

        # ── 言語
        section(t("sec_language"))
        # UI言語
        lang_var = tk.StringVar(value=self.settings.get("language", "ja"))
        lang_row = tk.Frame(inner)
        lang_row.pack(fill=tk.X, padx=36, pady=(2, 0))
        tk.Label(lang_row, text=t("lbl_ui_lang"),
                 font=("Yu Gothic", 9)).pack(side=tk.LEFT, padx=(0, 8))
        tk.Radiobutton(lang_row, text="English", variable=lang_var, value="en",
                       font=("Yu Gothic", 10)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(lang_row, text="日本語", variable=lang_var, value="ja",
                       font=("Yu Gothic", 10)).pack(side=tk.LEFT)

        # 転写言語（UI言語と独立）
        wlang_row = tk.Frame(inner)
        wlang_row.pack(fill=tk.X, padx=36, pady=(2, 4))
        tk.Label(wlang_row, text=t("lbl_whisper_lang"),
                 font=("Yu Gothic", 9)).pack(side=tk.LEFT, padx=(0, 8))
        wlang_var = tk.StringVar(value=self.settings.get("whisper_language", "ja"))
        for val, key in (("en", "wlang_en"), ("ja", "wlang_ja"), ("auto", "wlang_auto")):
            tk.Radiobutton(wlang_row, text=t(key), variable=wlang_var, value=val,
                           font=("Yu Gothic", 9)).pack(side=tk.LEFT, padx=(0, 10))

        separator()

        # ── エディター配色
        section(t("sec_color"))
        color_defs = [
            ("editor_fg",     t("color_fg")),
            ("editor_bg",     t("color_bg")),
            ("editor_cursor", t("color_cursor")),
        ]
        color_vars    = {}
        color_previews = {}  # プレビューラベルへの参照
        for key, label in color_defs:
            row = tk.Frame(inner, bg=inner.cget("bg"))
            row.pack(fill=tk.X, padx=36, pady=3)
            color_vars[key] = tk.StringVar(value=self.settings.get(key, DEFAULT_SETTINGS[key]))

            tk.Label(row, text=label, font=("Yu Gothic", 9), width=10, anchor=tk.W
                     ).pack(side=tk.LEFT)

            preview = tk.Label(row, width=4, relief=tk.SOLID, bd=1)
            preview.pack(side=tk.LEFT, padx=(0, 6))
            color_previews[key] = preview  # 参照を保存

            code_label = tk.Label(row, textvariable=color_vars[key],
                                  font=("Yu Gothic", 9), fg="#888899")
            code_label.pack(side=tk.LEFT, padx=(0, 8))

            def _make_picker(k, pv, sv):
                def pick():
                    result = colorchooser.askcolor(
                        color=sv.get(), title=t("color_dlg_title"), parent=dlg)
                    if result and result[1]:
                        sv.set(result[1])
                        pv.configure(bg=result[1])
                return pick

            preview.configure(bg=color_vars[key].get())
            tk.Button(row, text=t("btn_color_pick"), font=("Yu Gothic", 9),
                      bg="#2a2a44", fg="#ccccee", relief=tk.FLAT, padx=6,
                      command=_make_picker(key, preview, color_vars[key])
                      ).pack(side=tk.LEFT)

        # プリセット
        preset_row = tk.Frame(inner, bg=inner.cget("bg"))
        preset_row.pack(fill=tk.X, padx=36, pady=(2, 4))
        tk.Label(preset_row, text=t("lbl_preset"), font=("Yu Gothic", 9)
                 ).pack(side=tk.LEFT)
        presets = [
            (t("preset_dark"),    "#c8c8e0", "#0d0d1a", "#8888ff"),
            (t("preset_green"),   "#00ff88", "#0a1a0a", "#44ffaa"),
            (t("preset_amber"),   "#ffb347", "#1a0f00", "#ffcc66"),
        ]
        for name, fg, bg, cur in presets:
            def _make_preset(f, b, c):
                def apply_preset():
                    color_vars["editor_fg"].set(f)
                    color_vars["editor_bg"].set(b)
                    color_vars["editor_cursor"].set(c)
                    # プレビューラベルも即時更新
                    color_previews["editor_fg"].configure(bg=f)
                    color_previews["editor_bg"].configure(bg=b)
                    color_previews["editor_cursor"].configure(bg=c)
                return apply_preset
            tk.Button(preset_row, text=name, font=("Yu Gothic", 8),
                      bg=bg, fg=fg, relief=tk.FLAT, padx=6, pady=1,
                      command=_make_preset(fg, bg, cur)
                      ).pack(side=tk.LEFT, padx=2)

        separator()

        # ── 折り返しモード
        section(t("sec_wrap"))
        wrap_var = tk.StringVar(value=self.settings["wrap_mode"])
        for key in ("char", "word", "none"):
            tk.Radiobutton(inner, text=t(f"wrap_{key}"), variable=wrap_var, value=key,
                           font=("Yu Gothic", 10), anchor=tk.W
                           ).pack(fill=tk.X, padx=36, pady=1)

        separator()

        # ── フォントサイズ
        section(t("sec_fontsize"))
        size_frame = tk.Frame(inner)
        size_frame.pack(fill=tk.X, padx=36)
        size_var = tk.IntVar(value=self.settings["font_size"])
        tk.Spinbox(size_frame, from_=8, to=24, textvariable=size_var,
                   width=5, font=("Yu Gothic", 10)).pack(side=tk.LEFT)
        tk.Label(size_frame, text=t("lbl_pt"), font=("Yu Gothic", 10)).pack(side=tk.LEFT, padx=4)

        separator()

        # ── 録音中のシステム音量（メインUIの変数を共用 → 即時反映）
        section(t("sec_mic_mute"))
        tk.Checkbutton(
            inner, text=t("chk_mic_mute"),
            variable=self.mic_mute_var,
            font=("Yu Gothic", 9), anchor=tk.W,
        ).pack(fill=tk.X, padx=36, pady=(2, 4))

        mic_vol_frame = tk.Frame(inner)
        mic_vol_frame.pack(fill=tk.X, padx=52, pady=(0, 4))
        tk.Label(mic_vol_frame, text=t("lbl_mic_volume"),
                 font=("Yu Gothic", 9)).pack(side=tk.LEFT)
        tk.Label(mic_vol_frame, text=t("lbl_mic_vol_off"),
                 font=("Yu Gothic", 8), fg="#888899").pack(side=tk.LEFT, padx=(6, 0))
        tk.Scale(mic_vol_frame, variable=self.mic_vol_var,
                 from_=0, to=100, orient=tk.HORIZONTAL, length=180,
                 font=("Yu Gothic", 8),
                 showvalue=True).pack(side=tk.LEFT, padx=4)
        tk.Label(mic_vol_frame, text=t("lbl_mic_vol_on"),
                 font=("Yu Gothic", 8), fg="#888899").pack(side=tk.LEFT)

        separator()

        # ── 転写精度
        section(t("sec_transcript"))
        uctx_var = tk.BooleanVar(value=self.settings.get("use_user_context", True))
        tk.Checkbutton(
            inner,
            text=t("chk_user_ctx"),
            variable=uctx_var,
            font=("Yu Gothic", 9), anchor=tk.W,
        ).pack(anchor=tk.W, padx=36, pady=(2, 0))
        prompt_var = tk.BooleanVar(value=self.settings.get("use_initial_prompt", False))
        tk.Checkbutton(
            inner,
            text=t("chk_prompt"),
            variable=prompt_var,
            font=("Yu Gothic", 9), anchor=tk.W,
        ).pack(anchor=tk.W, padx=36, pady=(2, 0))
        tk.Label(
            inner, text=t("chk_prompt_note"),
            font=("Yu Gothic", 8), fg="#888899", anchor=tk.W,
            wraplength=380, justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=52, pady=(0, 2))

        separator()

        # ── モデル管理
        cuda_available = torch.cuda.is_available()
        section(t("sec_model_mode"))
        mode_var = tk.StringVar(value=self.settings["model_mode"])
        modes = [
            ("gpu",    t("mode_gpu")),
            ("cpu",    t("mode_cpu")),
        ]
        radio_btns = []
        for val, label in modes:
            rb = tk.Radiobutton(inner, text=label, variable=mode_var, value=val,
                                font=("Yu Gothic", 9), anchor=tk.W,
                                state=tk.NORMAL if cuda_available else tk.DISABLED)
            rb.pack(fill=tk.X, padx=36, pady=1)
            radio_btns.append(rb)

        # CPU待機のタイミング
        delay_frame = tk.Frame(inner)
        delay_frame.pack(fill=tk.X, padx=52, pady=(4, 0))
        delay_time_label = tk.Label(delay_frame, text=t("lbl_offload_time"),
                                    font=("Yu Gothic", 9))
        delay_time_label.pack(side=tk.LEFT)
        delay_var = tk.StringVar(value=str(self.settings["cpu_offload_delay"]))
        delay_choices = {"0": t("delay_0"), "1": t("delay_1"), "5": t("delay_5"), "10": t("delay_10")}
        delay_menu = tk.OptionMenu(delay_frame, delay_var, *delay_choices.keys())
        delay_menu.configure(font=("Yu Gothic", 9))
        delay_menu.pack(side=tk.LEFT, padx=6)
        # delay_var表示を日本語ラベルに変換するだけの補助ラベル
        delay_label = tk.Label(delay_frame, font=("Yu Gothic", 9), fg="#666666")
        delay_label.pack(side=tk.LEFT)

        def update_delay_label(*_):
            delay_label.configure(text=delay_choices.get(delay_var.get(), ""))
            # CUDAなし: delay関連は常時無効。あり: CPU待機モード以外はグレーアウト
            if not cuda_available:
                state = tk.DISABLED
            else:
                state = tk.NORMAL if mode_var.get() == "cpu" else tk.DISABLED
            delay_menu.configure(state=state)
            delay_time_label.configure(state=state)

        mode_var.trace_add("write", update_delay_label)
        delay_var.trace_add("write", update_delay_label)
        update_delay_label()

        separator()

        # ── 送信モード
        section(t("sec_send_mode"))
        send_var = tk.StringVar(value=self.settings["send_mode"])
        tk.Radiobutton(inner, text=t("send_normal"),
                       variable=send_var, value="normal",
                       font=("Yu Gothic", 9), anchor=tk.W
                       ).pack(fill=tk.X, padx=36, pady=1)
        tk.Radiobutton(inner, text=t("send_slow"),
                       variable=send_var, value="slow",
                       font=("Yu Gothic", 9), anchor=tk.W
                       ).pack(fill=tk.X, padx=36, pady=1)

        slow_frame = tk.Frame(inner)
        slow_frame.pack(fill=tk.X, padx=52, pady=(4, 0))

        tk.Label(slow_frame, text=t("lbl_chunk"),
                 font=("Yu Gothic", 9)).pack(side=tk.LEFT)
        chunk_var = tk.IntVar(value=self.settings["chunk_size"])
        tk.Spinbox(slow_frame, from_=10, to=200, textvariable=chunk_var,
                   width=5, font=("Yu Gothic", 9)).pack(side=tk.LEFT, padx=4)
        tk.Label(slow_frame, text=t("lbl_interval"),
                 font=("Yu Gothic", 9)).pack(side=tk.LEFT)
        cdelay_var = tk.StringVar(value=str(self.settings["chunk_delay"]))
        tk.OptionMenu(slow_frame, cdelay_var, "0.1", "0.15", "0.2", "0.3"
                      ).pack(side=tk.LEFT, padx=4)
        tk.Label(slow_frame, text=t("lbl_sec"),
                 font=("Yu Gothic", 9)).pack(side=tk.LEFT)

        def update_slow_state(*_):
            state = tk.NORMAL if send_var.get() == "slow" else tk.DISABLED
            for w in slow_frame.winfo_children():
                try:
                    w.configure(state=state)
                except Exception:
                    pass
        send_var.trace_add("write", update_slow_state)
        update_slow_state()

        separator()

        # ── 編集バックエンド
        section(t("sec_backend"))
        backend_var = tk.StringVar(value=self.settings["edit_backend"])
        backends = [
            ("claude_cli", t("backend_cli")),
            ("ollama",     t("backend_ollama")),
            ("gemini",     t("backend_gemini")),
        ]
        for val, label in backends:
            tk.Radiobutton(inner, text=label, variable=backend_var, value=val,
                           font=("Yu Gothic", 9), anchor=tk.W
                           ).pack(fill=tk.X, padx=36, pady=1)

        # Ollama設定
        ollama_frame = tk.LabelFrame(inner, text=t("frm_ollama"),
                                     font=("Yu Gothic", 9), padx=8, pady=4)
        ollama_frame.pack(fill=tk.X, padx=36, pady=(4, 0))
        tk.Label(ollama_frame, text=t("lbl_model_name"), font=("Yu Gothic", 9)
                 ).grid(row=0, column=0, sticky=tk.W)
        ollama_model_var = tk.StringVar(value=self.settings["ollama_model"])
        tk.Entry(ollama_frame, textvariable=ollama_model_var,
                 font=("Yu Gothic", 9), width=20).grid(row=0, column=1, padx=4)
        tk.Label(ollama_frame, text=t("lbl_host"), font=("Yu Gothic", 9)
                 ).grid(row=1, column=0, sticky=tk.W, pady=(2,0))
        ollama_host_var = tk.StringVar(value=self.settings["ollama_host"])
        tk.Entry(ollama_frame, textvariable=ollama_host_var,
                 font=("Yu Gothic", 9), width=28).grid(row=1, column=1, padx=4)

        # Gemini設定
        gemini_frame = tk.LabelFrame(inner, text=t("frm_gemini"),
                                     font=("Yu Gothic", 9), padx=8, pady=4)
        gemini_frame.pack(fill=tk.X, padx=36, pady=(4, 0))
        tk.Label(gemini_frame, text=t("lbl_api_key"), font=("Yu Gothic", 9)
                 ).grid(row=0, column=0, sticky=tk.W)
        gemini_key_var = tk.StringVar(value=self.settings.get("gemini_api_key", ""))
        tk.Entry(gemini_frame, textvariable=gemini_key_var, show="*",
                 font=("Yu Gothic", 9), width=28).grid(row=0, column=1, padx=4)
        tk.Label(gemini_frame, text=t("lbl_model"), font=("Yu Gothic", 9)
                 ).grid(row=1, column=0, sticky=tk.W, pady=(2,0))
        gemini_model_var = tk.StringVar(value=self.settings.get("gemini_model", "gemini-2.5-flash-lite"))
        tk.Entry(gemini_frame, textvariable=gemini_model_var,
                 font=("Yu Gothic", 9), width=28).grid(row=1, column=1, padx=4)

        def update_backend_frames(*_):
            b = backend_var.get()
            for w in ollama_frame.winfo_children():
                try: w.configure(state=tk.NORMAL if b=="ollama" else tk.DISABLED)
                except: pass
            for w in gemini_frame.winfo_children():
                try: w.configure(state=tk.NORMAL if b=="gemini" else tk.DISABLED)
                except: pass

        backend_var.trace_add("write", update_backend_frames)
        update_backend_frames()

        separator()

        def apply():
            self.settings["language"]           = lang_var.get()
            self.settings["wrap_mode"]         = wrap_var.get()
            self.settings["font_size"]          = size_var.get()
            self.settings["model_mode"]         = mode_var.get()
            self.settings["cpu_offload_delay"]  = int(delay_var.get())
            self.settings["send_mode"]          = send_var.get()
            self.settings["chunk_size"]         = chunk_var.get()
            self.settings["chunk_delay"]        = float(cdelay_var.get())
            self.settings["edit_backend"]       = backend_var.get()
            self.settings["ollama_model"]       = ollama_model_var.get()
            self.settings["ollama_host"]        = ollama_host_var.get()
            self.settings["gemini_api_key"]     = gemini_key_var.get()
            self.settings["gemini_model"]       = gemini_model_var.get()
            self.settings["use_user_context"]   = uctx_var.get()
            self.settings["use_initial_prompt"] = prompt_var.get()
            self._prompt_var.set(prompt_var.get())  # クイックトグルと同期
            self.settings["whisper_language"]   = wlang_var.get()
            # mic_mute_enabled / mic_mute_volume はメインUIで即時保存済み
            for key in ("editor_fg", "editor_bg", "editor_cursor"):
                self.settings[key] = color_vars[key].get()
            switch_lang(self.settings["language"])
            save_settings(self.settings)
            self.text.configure(
                wrap=WRAP_MAP[self.settings["wrap_mode"]],
                font=("Yu Gothic", self.settings["font_size"]),
                fg=self.settings["editor_fg"],
                bg=self.settings["editor_bg"],
                insertbackground=self.settings["editor_cursor"],
            )
            self._log(t("log_set_apply",
                        wrap=get_wrap_label(self.settings["wrap_mode"]),
                        size=self.settings["font_size"],
                        mode=self.settings["model_mode"]))
            dlg.destroy()

        tk.Button(inner, text=t("btn_apply"), command=apply,
                  bg="#2244aa", fg="white", font=("Yu Gothic", 10),
                  relief=tk.FLAT, padx=20).pack(pady=10)
        tk.Label(inner, text="ZikkoKey v1.0  —  © 2026 Tangerine Cirrus  —  MIT License",
                 font=("Yu Gothic", 8), fg="#445566").pack(pady=(0, 8))

    _PROMPT_PATH      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "initial_prompt.txt")
    _USER_CONTEXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_context.txt")

    def _reload_user_ctx_if_changed(self):
        """user_context.txt が更新されていれば再読み込みする"""
        try:
            mtime = os.path.getmtime(self._USER_CONTEXT_PATH)
            if mtime != self._user_ctx_mtime:
                content = open(self._USER_CONTEXT_PATH, encoding="utf-8").read().strip()
                self._user_ctx_content = content
                self._user_ctx_mtime   = mtime
        except (FileNotFoundError, OSError):
            self._user_ctx_content = ""
            self._user_ctx_mtime   = 0.0

    def _build_initial_prompt(self):
        """送信履歴から initial_prompt を構築して返す（末尾500文字）"""
        _CODE_SYM = set('{}[]<>();\\/|*#@$%^&~`')
        parts = []
        total = 0
        for entry in self.sent_history:  # 新しい順
            sym_ratio = sum(1 for c in entry if c in _CODE_SYM) / max(len(entry), 1)
            if sym_ratio >= 0.15:
                continue
            parts.append(entry)
            total += len(entry)
            if total >= 500:
                break
        history = "\n".join(reversed(parts)) if parts else ""  # 古い順に並べ直す（末尾が最新）
        return history[-500:] if history else None

    # ── ウィンドウ管理 ───────────────────────────────────────
    def _on_close(self):
        """ウィンドウを閉じる。最後の1枚なら全体を終了"""
        # 言語切替コールバックを解除
        if self._lang_cb in _lang_callbacks:
            _lang_callbacks.remove(self._lang_cb)
        # 録音・再生を停止
        if getattr(self, "recording", False):
            self.recording = False
            sd.stop()
        if getattr(self, "_playing", False):
            self._stop_audio()
        # initial_prompt をファイルに保存（次回起動時の文脈引き継ぎ用）
        if self.settings.get("use_initial_prompt", False):
            prompt = self._build_initial_prompt()
            if prompt:
                try:
                    with open(self._PROMPT_PATH, "w", encoding="utf-8") as f:
                        f.write(prompt)
                except Exception:
                    pass
        if self in _g.windows:
            _g.windows.remove(self)
        self.root.destroy()
        if not _g.windows:
            _splash_root.quit()

    def _open_new_window(self):
        """新しい入力ウィンドウを追加で開く"""
        InputWindow(root=tk.Toplevel(_splash_root))

    # ── ウィンドウ選択 ───────────────────────────────────────
    def _pick_window(self):
        wins = [(h, t) for h, t in get_all_windows()
                if t and t != self.root.title()]
        if not wins:
            messagebox.showinfo(t("dlg_no_wins_t"), t("dlg_no_wins"))
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(t("dlg_pick_win"))
        dlg.geometry("560x360")
        dlg.grab_set()
        dlg.attributes("-topmost", True)

        frame = tk.Frame(dlg)
        frame.pack(expand=True, fill=tk.BOTH, padx=10)
        sb = tk.Scrollbar(frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb = tk.Listbox(frame, font=("Yu Gothic", 10),
                        yscrollcommand=sb.set, selectmode=tk.SINGLE)
        lb.pack(expand=True, fill=tk.BOTH)
        sb.config(command=lb.yview)

        for _, title in wins:
            lb.insert(tk.END, title)
        for i, (_, ttl) in enumerate(wins):
            if "claude" in ttl.lower():
                lb.selection_set(i)
                lb.see(i)
                break

        def ok():
            sel = lb.curselection()
            if not sel:
                return
            idx = sel[0]
            self._register_target(wins[idx][0], wins[idx][1])
            self._log(t("log_set_target", title=wins[idx][1]))
            dlg.destroy()

        tk.Button(dlg, text=t("btn_select"), command=ok,
                  bg="#2244aa", fg="white", font=("Yu Gothic", 10),
                  relief=tk.FLAT, padx=20).pack(pady=8)

    # ── スクリーンショット ─────────────────────────────────────
    _SHOT_SKIP = {"program manager", "microsoft text input application",
                  "windows input experience", "デスクトップ センター"}

    def _render_shot_buttons(self):
        """shotbar を再描画する"""
        for w in self.shotbar.winfo_children():
            w.destroy()
        BG = "#0f1a0f"

        # 右端: ウィンドウ選択ダイアログボタン
        tk.Button(self.shotbar, textvariable=sv("btn_pick_win"),
                  command=self._add_shot_target,
                  bg="#1a4422", fg="#88cc88",
                  font=("Yu Gothic", 9),
                  relief=tk.FLAT, padx=8, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=(0, 8))

        # シャッター音チェックボックス（ウィンドウ選択の左）
        tk.Checkbutton(self.shotbar, textvariable=sv("chk_shutter"),
                       variable=self._shutter_sound_var,
                       bg=BG, fg="#88cc88", selectcolor=BG,
                       activebackground=BG, activeforeground="#aaddaa",
                       font=("Yu Gothic", 9), cursor="hand2"
                       ).pack(side=tk.RIGHT, padx=(0, 4))

        # 撮影先が無効になっていたら除去
        if self._screenshot_target and not user32.IsWindow(self._screenshot_target[0]):
            self._screenshot_target = None

        # 左端: 📸 ボタン（撮影先設定済み→即キャプチャ、未設定→ピッキング開始）
        if self._screenshot_target:
            self._shot_add_btn = tk.Button(
                self.shotbar, textvariable=sv("btn_screenshot"),
                command=lambda: self._do_capture(self._screenshot_target[0]),
                bg="#224422", fg="#aaddaa",
                font=("Yu Gothic", 9), relief=tk.FLAT, padx=8, cursor="hand2")
        else:
            self._shot_add_btn = tk.Button(
                self.shotbar, textvariable=sv("btn_screenshot"),
                command=self._toggle_shot_picking,
                bg="#224422", fg="#aaddaa",
                font=("Yu Gothic", 9), relief=tk.FLAT, padx=8, cursor="hand2")
        self._shot_add_btn.pack(side=tk.LEFT, padx=(8, 4))

        # 「撮影先:」ボタン（常に表示）
        # ピッキング中 → 「キャンセル」として機能
        # 通常時 → クリックでピッキングモード開始
        if self._shot_picking:
            tk.Button(self.shotbar, textvariable=sv("btn_shot_cancel"),
                      command=self._toggle_shot_picking,
                      bg="#aa4400", fg="white",
                      font=("Yu Gothic", 9), relief=tk.FLAT, padx=8, cursor="hand2"
                      ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(self.shotbar, textvariable=sv("hint_shot_picking"),
                     bg=BG, fg="#cc6622", font=("Yu Gothic", 9)
                     ).pack(side=tk.LEFT, padx=4)
        else:
            tk.Button(self.shotbar, textvariable=sv("lbl_shot_bar"),
                      command=self._toggle_shot_picking,
                      bg=BG, fg="#88cc88", activebackground=BG,
                      font=("Yu Gothic", 9), relief=tk.FLAT, cursor="hand2"
                      ).pack(side=tk.LEFT, padx=(0, 2))
            if self._screenshot_target:
                _, title = self._screenshot_target
                label = title[:39] + "…" if len(title) > 39 else title
                tk.Label(self.shotbar, text=label, bg=BG, fg="#aaddaa",
                         font=("Yu Gothic", 9)).pack(side=tk.LEFT)
            else:
                tk.Label(self.shotbar, textvariable=sv("hint_no_shots"),
                         bg=BG, fg="#445544", font=("Yu Gothic", 9)
                         ).pack(side=tk.LEFT, padx=4)

    def _toggle_shot_picking(self):
        """ピッキングモードの ON/OFF トグル"""
        if self._shot_picking:
            self._shot_picking = False
            if self._shot_pick_id:
                self.root.after_cancel(self._shot_pick_id)
                self._shot_pick_id = None
            self._render_shot_buttons()
        else:
            self._shot_picking = True
            self._render_shot_buttons()
            self._poll_shot_foreground(prev_hwnd=user32.GetForegroundWindow())

    def _poll_shot_foreground(self, prev_hwnd):
        """ピッキングモード中: フォアグラウンドが変わったら撮影先に設定してキャプチャ"""
        if not self._shot_picking:
            return
        hwnd = user32.GetForegroundWindow()
        our_hwnds = {w.root.winfo_id() for w in _g.windows}
        if hwnd and hwnd != prev_hwnd and hwnd not in our_hwnds:
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
                if title.lower() not in self._SHOT_SKIP:
                    self._shot_picking = False
                    self._shot_pick_id = None
                    self._screenshot_target = (hwnd, title)
                    self._shot_suppress_until = __import__("time").time() + 0.6
                    self._render_shot_buttons()
                    self._do_capture(hwnd)
                    return
        self._shot_pick_id = self.root.after(
            150, lambda: self._poll_shot_foreground(
                prev_hwnd=prev_hwnd if hwnd in our_hwnds else hwnd))

    def _add_shot_target(self):
        """ウィンドウ選択ダイアログで撮影先を設定"""
        all_wins = [
            (h, ttl) for h, ttl in get_all_windows()
            if ttl and ttl != self.root.title()
            and ttl.lower() not in self._SHOT_SKIP
        ]
        if not all_wins:
            messagebox.showinfo("", t("shot_no_chrome"))
            return
        auto = [(h, ttl) for h, ttl in all_wins
                if "app_structure" in ttl.lower() or "改修ポンチ絵" in ttl]

        result = [None]
        dlg = tk.Toplevel(self.root)
        dlg.title(t("dlg_screenshot"))
        dlg.geometry("580x340")
        dlg.grab_set()
        dlg.attributes("-topmost", True)
        frame = tk.Frame(dlg)
        frame.pack(expand=True, fill=tk.BOTH, padx=10)
        sb = tk.Scrollbar(frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb = tk.Listbox(frame, font=("Yu Gothic", 10),
                        yscrollcommand=sb.set, selectmode=tk.SINGLE)
        lb.pack(expand=True, fill=tk.BOTH)
        sb.config(command=lb.yview)
        for _, ttl in all_wins:
            lb.insert(tk.END, ttl)
        if auto:
            for i, (h, _) in enumerate(all_wins):
                if h == auto[0][0]:
                    lb.selection_set(i); lb.see(i); break
        else:
            lb.selection_set(0)

        def ok():
            sel = lb.curselection()
            if sel:
                result[0] = all_wins[sel[0]]
            dlg.destroy()

        tk.Button(dlg, text=t("btn_select"), command=ok,
                  bg="#2244aa", fg="white", font=("Yu Gothic", 10),
                  relief=tk.FLAT, padx=20).pack(pady=8)
        dlg.bind("<Return>", lambda _: ok())
        dlg.wait_window()

        if result[0]:
            hwnd, title = result[0]
            self._screenshot_target = (hwnd, title)
            self._shot_suppress_until = __import__("time").time() + 0.6
            self._render_shot_buttons()
            self._do_capture(hwnd)

    def _do_capture(self, hwnd):
        """対象ウィンドウを最前面に出してから shot.png に保存"""
        if not user32.IsWindow(hwnd):
            self._render_shot_buttons()
            return
        # このアプリの最前面を一時解除してから撮影
        self.root.attributes("-topmost", False)
        activate_hwnd(hwnd)
        self.root.after(300, lambda: self._do_capture_impl(hwnd))

    def _do_capture_impl(self, hwnd):
        """キャプチャ本体（activate_hwnd から 300ms 後に呼ばれる）"""
        if not user32.IsWindow(hwnd):
            return
        rect = ctypes.wintypes.RECT()
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        dwmapi = ctypes.windll.dwmapi
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect))
        if hr != 0:
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
        bbox = (rect.left, rect.top, rect.right, rect.bottom)
        if bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
            messagebox.showinfo("", "ウィンドウサイズが取得できませんでした")
            return
        try:
            from PIL import ImageGrab
        except ImportError:
            messagebox.showerror("", "Pillow が必要です: pip install Pillow")
            return
        img = ImageGrab.grab(bbox=bbox)
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "shot.png")
        img.save(out_path)
        # 最前面を設定に従って復元
        self.root.attributes("-topmost", self._topmost_var.get())
        self.root.clipboard_clear()
        self.root.clipboard_append(out_path)
        self._log(t("shot_saved", path=out_path))
        self._play_shutter_sound()

    def _play_shutter_sound(self):
        """CameraShutter2.mp3 をバックグラウンド再生（winmm MCI 経由）"""
        if not self._shutter_sound_var.get():
            return
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CameraShutter2.mp3")
        if not os.path.exists(path):
            return
        def _play():
            winmm = ctypes.windll.winmm
            alias = "shutter_snd"
            winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
            winmm.mciSendStringW(f'play {alias} wait', None, 0, None)
            winmm.mciSendStringW(f'close {alias}', None, 0, None)
        threading.Thread(target=_play, daemon=True).start()

    # ── 右Ctrl ──────────────────────────────────────────────
    def _on_rctrl(self, event):
        self._log(t("log_rctrl"))
        self.send()
        return "break"

    # ── 送信 ─────────────────────────────────────────────────
    def send(self):
        text = self.text.get("1.0", tk.END).rstrip("\n")

        if not self.target_hwnd:
            messagebox.showwarning(t("dlg_no_target_t"), t("dlg_no_target"))
            return

        empty = not text.strip()
        info = t("log_send_empty") if empty else t("log_send_chars", n=len(text))
        self._log(t("log_send", info=info, target=self.target_title[:40]))
        self.status.set(t("status_sending"))
        self.root.update()

        if not empty:
            try:
                pyperclip.copy(text)
            except Exception as e:
                self._log(t("log_err_clip", e=e))
                messagebox.showerror(t("dlg_clip_err_t"), str(e))
                return

        try:
            # 右Ctrlが物理的に押された状態でウィンドウを切り替えると
            # ターミナル側がキーアップを受け取り先頭文字が欠落するため先に離す
            # 修飾キーを全て解放してからウィンドウ切り替え
            for key in ("ctrl", "shift", "alt"):
                pyautogui.keyUp(key)
            activate_hwnd(self.target_hwnd)
            time.sleep(0.4)
            if not empty:
                if self.settings.get("send_mode") == "slow":
                    self._paste_chunked(text)
                else:
                    pyautogui.keyDown("ctrl")
                    time.sleep(0.05)
                    pyautogui.press("v")
                    time.sleep(0.05)
                    pyautogui.keyUp("ctrl")
                    time.sleep(0.2)
            pyautogui.press("enter")
            self._log(t("log_send_ok"))
        except Exception as e:
            self._log(t("log_err_send", e=e))
            messagebox.showerror(t("dlg_send_err_t"), str(e))
            self.status.set(t("status_error"))
            self.root.after(2000, lambda: self.status.set(t("status_ready")))
            return

        # 送信履歴に保存（空送信は除く）
        if not empty:
            self.sent_history.insert(0, text)
            if len(self.sent_history) > self.HISTORY_MAX:
                self.sent_history.pop()

        self._redo_stack.clear()   # 送信したらredoスタックをクリア
        self.text.delete("1.0", tk.END)
        self.text.edit_reset()   # 送信後のundo履歴をクリア
        self.status.set(t("status_sent"))
        self.root.after(300, self.root.focus_force)
        self.root.after(2500, lambda: self.status.set(t("status_ready")))


if __name__ == "__main__":
    InputWindow()
    try:
        _splash_root.mainloop()
    except KeyboardInterrupt:
        pass
