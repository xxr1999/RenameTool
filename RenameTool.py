# -*- coding: utf-8 -*-
"""RenameTool.py

tkinter 高级批量重命名工具（只处理文件，绝不重命名文件夹）。

运行：
    python RenameTool.py

依赖：
    - 运行主体仅使用 Python 标准库（tkinter / os / re / datetime / pathlib 等）
    - tkinterdnd2 为可选依赖，仅用于启用拖拽功能；未安装时拖拽自动禁用，不报错
"""

from __future__ import annotations

import datetime
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# 可选依赖：tkinterdnd2（拖拽）。缺失时 DND_AVAILABLE = False，功能直接禁用。
# ---------------------------------------------------------------------------
DND_AVAILABLE = False
DND_FILES = None
TkinterDnD = None
try:  # pragma: no cover - 取决于运行环境
    from tkinterdnd2 import DND_FILES as _DND_FILES
    from tkinterdnd2 import TkinterDnD as _TkinterDnD

    DND_FILES = _DND_FILES
    TkinterDnD = _TkinterDnD
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
APP_TITLE = "RenameTool - 高级批量重命名"
# 版本标记：标题栏会显示它，用来确认你运行的就是带 {num} 独立全局计数器的修复版
VERSION_TAG = "v2.2-{num}计数器-修复.*重复替换"
READY_HINT = "就绪，请添加文件"
DND_DISABLED_HINT = "拖拽功能未启用，如需请安装 tkinterdnd2"
DND_ENABLED_HINT = "拖拽已启用：可直接拖入文件或文件夹"
MANY_FILES_HINT = "文件较多，预览刷新可能较慢"
REGEX_ERROR_HINT = "正则表达式无效"

# {num} 占位符（全局编号计数器专用，与旧的“启用顺序编号”末尾追加模块完全独立）
NUM_TOKEN = "{num}"
NUM_TOKEN_ESCAPED = "{{num}}"
NUM_TOKEN_SENTINEL = "\x00~num-escaped~\x00"
PH_ACTIVE_HINT = "已启用 {num} 占位符计数器"

ILLEGAL_CHAR_SET = set('\\/:*?"<>|')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"}
RESERVED_NAMES |= {"COM%d" % i for i in range(1, 10)}
RESERVED_NAMES |= {"LPT%d" % i for i in range(1, 10)}

MAX_PATH_LEN = 255
DEFAULT_TIME_FORMAT = "YYYYMMDD_HHMMSS"
STRFTIME_FALLBACK = "%Y%m%d_%H%M%S"

SORT_LABELS = ("表格顺序", "文件名称", "修改时间", "文件大小")
SORT_KEYS = {"表格顺序": "table", "文件名称": "name", "修改时间": "mtime", "文件大小": "size"}
TIME_LABELS = ("不插入时间", "修改时间", "创建时间")
TIME_KEYS = {"不插入时间": "none", "修改时间": "mtime", "创建时间": "ctime"}

PREVIEW_DEBOUNCE_MS = 200
MANY_FILES_THRESHOLD = 500
PROGRESS_STEP = 10


# ===========================================================================
# 纯逻辑层（不依赖 Tk，可单独测试）
# ===========================================================================
def norm_key(path: str) -> str:
    """用于去重 / 比较的规范化绝对路径键（Windows 下大小写不敏感）。"""
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return os.path.normcase(path)


def parse_int_strict(text: str):
    """严格解析整数（允许正负号）；非法返回 None。"""
    if text is None:
        return None
    t = str(text).strip()
    if not re.fullmatch(r"[+-]?\d+", t):
        return None
    try:
        return int(t)
    except ValueError:
        return None


def parse_suffix_filter(text: str):
    """把后缀筛选输入框解析为后缀列表；留空返回 None 表示关闭筛选。"""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    parts = []
    for item in re.split(r"[,，;；\s]+", raw):
        s = item.strip().lstrip("*").lstrip(".").lower()
        if s and s not in parts:
            parts.append(s)
    return parts or None


def name_matches_filter(name: str, filters) -> bool:
    """filters 为 None 时表示不筛选。"""
    if not filters:
        return True
    ext = os.path.splitext(name)[1]
    ext = ext[1:].lower() if ext.startswith(".") else ext.lower()
    return ext in filters


def normalize_time_format(fmt: str) -> str:
    """时间格式兼容处理。

    - 含 % 时按 Python strftime 原生行为直接使用；
    - 不含 % 时把 YYYY/YY/MM/DD/HH/SS 这类友好写法转换为 strftime 记号，
      其中第一次出现的 MM 视为月份，之后出现的 MM 视为分钟。
    """
    if not fmt:
        return STRFTIME_FALLBACK
    fmt = str(fmt)
    if "%" in fmt:
        return fmt
    out = []
    i = 0
    n = len(fmt)
    up = fmt.upper()
    month_used = False
    while i < n:
        if up.startswith("YYYY", i):
            out.append("%Y")
            i += 4
        elif up.startswith("YY", i):
            out.append("%y")
            i += 2
        elif up.startswith("MM", i):
            if month_used:
                out.append("%M")
            else:
                out.append("%m")
                month_used = True
            i += 2
        elif up.startswith("DD", i):
            out.append("%d")
            i += 2
        elif up.startswith("HH", i):
            out.append("%H")
            i += 2
        elif up.startswith("SS", i):
            out.append("%S")
            i += 2
        else:
            out.append(fmt[i])
            i += 1
    return "".join(out) or STRFTIME_FALLBACK


def format_number(value: int, pad: int) -> str:
    """按补零位数格式化编号，负数保留负号且总宽度对齐。"""
    try:
        value = int(value)
        pad = int(pad)
    except (TypeError, ValueError):
        return str(value)
    if pad < 1:
        pad = 1
    if value < 0:
        return "-" + str(abs(value)).zfill(max(1, pad - 1))
    return str(value).zfill(pad)


def num_token_used(*texts) -> bool:
    """判断文本中是否真正引用了 {num}；{{num}} 是转义写法，不算引用。"""
    for text in texts:
        if not text:
            continue
        if NUM_TOKEN in text.replace(NUM_TOKEN_ESCAPED, ""):
            return True
    return False


def expand_num_token(text: str, value_str: str) -> str:
    """把 {num} 展开为全局计数器的编号字符串。

    - {{num}} 视为转义，输出字面量 {num}，不受计数器影响；
    - 没有引用 {num} 时原样返回，绝不自动追加任何字符。
    """
    if not text or NUM_TOKEN not in text:
        return text
    out = text.replace(NUM_TOKEN_ESCAPED, NUM_TOKEN_SENTINEL)
    out = out.replace(NUM_TOKEN, value_str)
    return out.replace(NUM_TOKEN_SENTINEL, NUM_TOKEN)


def regex_replace(pattern, repl: str, text: str) -> str:
    """执行 pattern.sub(repl, text)，并消除一个批量改名里很常见的坑。

    `.*`、`.*$`、`^.*` 这类既能匹配整串、又能匹配空串的模式，在整串匹配之后还会在
    字符串末尾再产生一次“空匹配”，re.sub 因此会把替换串插入两次
    （替换为 {num} 时会得到 11、22、33…，而用户要的是 1、2、3…）。
    确认属于这种情形时，只保留一次替换结果。
    """
    result = pattern.sub(repl, text)
    if not text:
        return result
    first = pattern.match(text)
    if first is not None and first.span() == (0, len(text)) \
            and pattern.match("") is not None:
        return first.expand(repl)
    return result


def creation_time_supported() -> bool:
    """判断当前系统是否能获取文件创建时间。"""
    if os.name == "nt":  # Windows: st_ctime 即创建时间
        return True
    try:
        st = os.stat(os.getcwd())
    except OSError:
        return False
    return hasattr(st, "st_birthtime")


class FileEntry:
    """表格中的一条文件记录。"""

    __slots__ = ("path", "dir", "name", "mtime", "ctime", "size", "order", "ctime_ok")

    def __init__(self, abs_path: str, order: int):
        self.path = abs_path
        self.dir = os.path.dirname(abs_path)
        self.name = os.path.basename(abs_path)
        self.mtime = 0.0
        self.ctime = 0.0
        self.size = 0
        self.order = order
        self.ctime_ok = False
        self.refresh_stat()

    def refresh_stat(self) -> bool:
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        self.mtime = st.st_mtime
        self.size = st.st_size
        if hasattr(st, "st_birthtime"):
            self.ctime = st.st_birthtime
            self.ctime_ok = True
        elif os.name == "nt":
            self.ctime = st.st_ctime
            self.ctime_ok = True
        else:
            self.ctime = st.st_mtime
            self.ctime_ok = False
        return True

    def time_for(self, mode: str) -> float:
        """mode: 'mtime' / 'ctime'；创建时间不可用时自动降级为修改时间。"""
        if mode == "ctime" and self.ctime_ok:
            return self.ctime
        return self.mtime


class Rules:
    """一次预览 / 执行所使用的全部改名规则快照。"""

    __slots__ = (
        "regex_enabled", "regex", "regex_repl", "regex_include_ext", "regex_case_sensitive",
        "prefix", "suffix",
        "number_enabled", "start", "step", "pad", "sort_key",
        "ph_start", "ph_step", "ph_pad", "ph_sort_key", "ph_active",
        "time_mode", "time_format",
    )

    def __init__(self):
        self.regex_enabled = False
        self.regex = ""
        self.regex_repl = ""
        self.regex_include_ext = False
        self.regex_case_sensitive = False
        self.prefix = ""
        self.suffix = ""
        self.number_enabled = False
        self.start = 0
        self.step = 1
        self.pad = 3
        self.sort_key = "table"
        # 独立的全局编号计数器：只用于解析 {num} 占位符，不追加任何字符
        self.ph_start = 1
        self.ph_step = 1
        self.ph_pad = 1
        self.ph_sort_key = "table"
        self.ph_active = False
        self.time_mode = "none"
        self.time_format = DEFAULT_TIME_FORMAT


def apply_rules(entry: FileEntry, rules: Rules, pattern, seq, ph_value=None) -> str:
    """按固定顺序生成新文件名：正则替换 → 前缀后缀 → 顺序编号(末尾追加) → 时间插入。

    独立的全局计数器编号通过 ph_value 传入，仅用于展开 {num} 占位符：
    占位符出现在哪里就替换在哪里，程序绝不会自动追加数字或下划线。
    """
    name = entry.name
    stem, ext = os.path.splitext(name)

    ph_text = format_number(ph_value, rules.ph_pad) if ph_value is not None else ""

    # 1) 正则替换（替换串中可以写 {num}）
    if pattern is not None:
        repl = expand_num_token(rules.regex_repl, ph_text)
        if rules.regex_include_ext:
            full = regex_replace(pattern, repl, name)
            stem, ext = os.path.splitext(full)
        else:
            stem = regex_replace(pattern, repl, stem)

    # 2) 前缀 / 后缀（其中可以写 {num}）
    prefix = expand_num_token(rules.prefix, ph_text)
    suffix = expand_num_token(rules.suffix, ph_text)
    if prefix:
        stem = prefix + stem
    if suffix:
        stem = stem + suffix

    # 3) 旧的顺序编号追加模块（行为保持不变：末尾追加 "_数字"）
    if rules.number_enabled and seq is not None:
        stem = stem + "_" + format_number(seq, rules.pad)

    # 4) 时间插入（编号在前、时间在后）
    if rules.time_mode != "none":
        ts = entry.time_for(rules.time_mode)
        try:
            dt = datetime.datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            dt = datetime.datetime.now()
        fmt = normalize_time_format(rules.time_format)
        try:
            text = dt.strftime(fmt)
        except (ValueError, TypeError):
            text = dt.strftime(STRFTIME_FALLBACK)
        stem = stem + "_" + text

    return stem + ext


def sort_entries_for_numbering(entries, sort_key):
    """按排序依据返回编号分配顺序（稳定排序；表格顺序=文件加入表格的先后顺序）。"""
    ordered = list(entries)
    if sort_key == "name":
        ordered.sort(key=lambda e: e.name.lower())  # 不区分大小写普通字符串排序
    elif sort_key == "mtime":
        ordered.sort(key=lambda e: e.mtime)
    elif sort_key == "size":
        ordered.sort(key=lambda e: e.size)
    return ordered


def compute_preview(entries, rules: Rules):
    """返回 [(entry, new_name), ...]，顺序与传入 entries（表格显示顺序）一致。"""
    results = []

    pattern = None
    if rules.regex_enabled:
        flags = 0 if rules.regex_case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(rules.regex, flags)
        except re.error:
            return None  # 调用方据此判定“正则表达式无效”

    # 旧的“启用顺序编号”模块：仅在勾选时分配追加编号
    seq_map = {}
    if rules.number_enabled:
        for i, e in enumerate(sort_entries_for_numbering(entries, rules.sort_key)):
            seq_map[id(e)] = rules.start + i * rules.step

    # 独立的全局编号计数器：始终计算，与“启用顺序编号”复选框无关，
    # 只用于展开 {num} 占位符，不追加任何字符。
    ph_map = {}
    for i, e in enumerate(sort_entries_for_numbering(entries, rules.ph_sort_key)):
        ph_map[id(e)] = rules.ph_start + i * rules.ph_step

    try:
        for e in entries:
            results.append((e, apply_rules(e, rules, pattern,
                                           seq_map.get(id(e)), ph_map.get(id(e)))))
    except re.error:
        # 例如替换串里写了不存在的捕获组 \2，只有真正替换时才会报错
        return None
    return results


def validate_results(results, batch_entries):
    """预览阶段校验。

    返回 (problems, groups)：
      problems: {id(entry): {'duplicate','illegal','reserved','toolong'}}
      groups:   {(目录, 新名小写): [entry, ...]}
    """
    moving = {norm_key(e.path) for e, new in results if new != e.name}
    problems = {}
    groups = {}

    for e, new_name in results:
        codes = set()
        if not new_name or new_name in (".", ".."):
            codes.add("illegal")
        if any(ch in ILLEGAL_CHAR_SET for ch in new_name):
            codes.add("illegal")
        if new_name != new_name.rstrip(" ."):
            codes.add("illegal")  # Windows 下文件名不能以空格或点结尾
        base = os.path.splitext(new_name)[0]
        if base.upper() in RESERVED_NAMES or new_name.upper() in RESERVED_NAMES:
            codes.add("reserved")
        full = os.path.join(e.dir, new_name)
        if len(full) > MAX_PATH_LEN:
            codes.add("toolong")
        groups.setdefault((os.path.normcase(e.dir), new_name.lower()), []).append(e)
        if codes:
            problems.setdefault(id(e), set()).update(codes)

    # 1) 重名冲突：同一目录下多个文件生成相同新名
    for _key, lst in groups.items():
        if len(lst) > 1:
            for e in lst:
                problems.setdefault(id(e), set()).add("duplicate")

    # 目标名已被磁盘上其它文件（不在本批次中移动的文件）占用
    for e, new_name in results:
        full = os.path.join(e.dir, new_name)
        nk = norm_key(full)
        if nk == norm_key(e.path):
            continue  # 名字没变，属于自己
        if nk in moving:
            continue  # 该目标正是本批次里会被改走的文件，交给执行顺序处理
        if os.path.exists(full):
            problems.setdefault(id(e), set()).add("duplicate")

    return problems, groups


def order_rename_ops(ops):
    """给改名操作排序，尽量先处理“目标名不是本批次其它文件现用名”的操作，
    从而支持 A→B、B→C 这类链式改名。出现环时保持原顺序（执行失败会回滚）。"""
    pending = list(ops)
    sources = {norm_key(e.path) for e, _new in pending}
    ordered = []
    while pending:
        picked_index = None
        for i, (e, new_name) in enumerate(pending):
            target = norm_key(os.path.join(e.dir, new_name))
            if target not in sources:
                picked_index = i
                break
        if picked_index is None:
            ordered.extend(pending)
            break
        e, new_name = pending.pop(picked_index)
        sources.discard(norm_key(e.path))
        ordered.append((e, new_name))
    return ordered


def collect_files_from_dir(dir_path: str, recursive: bool):
    """扫描目录中的文件（不含文件夹本身）。"""
    result = []
    if recursive:
        for root_dir, dirs, files in os.walk(dir_path):
            dirs.sort()
            for fn in sorted(files):
                result.append(os.path.join(root_dir, fn))
    else:
        try:
            names = sorted(os.listdir(dir_path))
        except OSError:
            return result
        for fn in names:
            full = os.path.join(dir_path, fn)
            if os.path.isfile(full):
                result.append(full)
    return result


# ===========================================================================
# 界面层
# ===========================================================================
class RenameToolApp:
    def __init__(self, root: tk.Misc):
        self.root = root
        self.root.title("%s  [%s]" % (APP_TITLE, VERSION_TAG))
        self.root.minsize(900, 650)
        self.root.geometry("1120x780")

        # ---------------- 数据 ----------------
        self.files = []            # 主集合：加入表格的文件（保持加入先后顺序）
        self.rows = []             # 当前表格中可见 / 待处理的文件（筛选后）
        self._index = {}           # norm_key(path) -> FileEntry，用于去重
        self._order_counter = 0
        self._results = []         # [(entry, new_name)] 最近一次预览结果
        self._preview_map = {}     # id(entry) -> new_name
        self._preview_paths = {}   # id(entry) -> 预览时的绝对路径
        self._preview_job = None
        self._pending_msg = ""
        self._can_confirm = False
        self._ctime_warned = False
        self.undo_record = []      # [(entry, old_path, new_path)]

        # ---------------- 变量 ----------------
        self.recursive_var = tk.BooleanVar(value=False)
        self.filter_var = tk.StringVar(value="")

        self.regex_enabled_var = tk.BooleanVar(value=False)
        self.regex_var = tk.StringVar(value="")
        self.regex_repl_var = tk.StringVar(value="")
        self.regex_ext_var = tk.BooleanVar(value=False)
        self.regex_case_var = tk.BooleanVar(value=False)

        self.prefix_var = tk.StringVar(value="")
        self.suffix_var = tk.StringVar(value="")

        self.number_enabled_var = tk.BooleanVar(value=False)
        self.start_var = tk.StringVar(value="1")
        self.step_var = tk.StringVar(value="1")
        self.pad_var = tk.StringVar(value="3")
        self.sort_var = tk.StringVar(value=SORT_LABELS[0])

        # 独立的全局编号计数器（{num} 占位符专用）
        self.ph_start_var = tk.StringVar(value="1")
        self.ph_step_var = tk.StringVar(value="1")
        self.ph_pad_var = tk.StringVar(value="1")
        self.ph_sort_var = tk.StringVar(value=SORT_LABELS[0])

        self.time_mode_var = tk.StringVar(value=TIME_LABELS[0])
        self.time_format_var = tk.StringVar(value=DEFAULT_TIME_FORMAT)

        self.status_var = tk.StringVar(value=READY_HINT)
        self.hint_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="")

        self._build_ui()
        self._bind_events()
        self._setup_dnd()
        self.update_undo_button()

        # 表格初始为空，不加载任何目录
        self.set_status(READY_HINT)
        self.update_count()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)  # 预览区占用多余空间

        # ① 文件选择与筛选区 -------------------------------------------------
        top = ttk.LabelFrame(root, text="① 文件选择与筛选")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        top.columnconfigure(5, weight=1)

        ttk.Button(top, text="选择文件夹...", command=self.choose_folder).grid(
            row=0, column=0, padx=(8, 4), pady=6)
        ttk.Button(top, text="添加文件...", command=self.choose_files).grid(
            row=0, column=1, padx=4, pady=6)
        ttk.Button(top, text="清空列表", command=self.clear_files).grid(
            row=0, column=2, padx=4, pady=6)
        ttk.Checkbutton(top, text="包含子文件夹", variable=self.recursive_var).grid(
            row=0, column=3, padx=(10, 4), pady=6)
        ttk.Label(top, text="文件后缀筛选（逗号分隔，如 jpg,png,txt；留空=不筛选）：").grid(
            row=0, column=4, padx=(12, 4), pady=6)
        ttk.Entry(top, textvariable=self.filter_var).grid(
            row=0, column=5, sticky="ew", padx=(0, 8), pady=6)

        ttk.Label(top, textvariable=self.count_var, foreground="#555555").grid(
            row=1, column=0, columnspan=6, sticky="w", padx=8, pady=(0, 6))

        # ② 改名规则配置区 ---------------------------------------------------
        rules_outer = ttk.LabelFrame(
            root, text="② 改名规则配置（固定顺序：正则替换 → 添加前缀后缀 → 顺序编号 → 时间插入）")
        rules_outer.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        rules_outer.columnconfigure(0, weight=1)
        rules_outer.columnconfigure(1, weight=1)

        # 1) 正则替换
        f_regex = ttk.LabelFrame(rules_outer, text="1) 正则替换")
        f_regex.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        f_regex.columnconfigure(1, weight=1)
        ttk.Checkbutton(f_regex, text="启用正则替换", variable=self.regex_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2))
        ttk.Label(f_regex, text="正则表达式：").grid(row=1, column=0, sticky="e", padx=6, pady=3)
        ttk.Entry(f_regex, textvariable=self.regex_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=3)
        ttk.Label(f_regex, text="替换为：").grid(row=2, column=0, sticky="e", padx=6, pady=3)
        ttk.Entry(f_regex, textvariable=self.regex_repl_var).grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=3)
        opt_row = ttk.Frame(f_regex)
        opt_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 6))
        ttk.Checkbutton(opt_row, text="正则包含扩展名", variable=self.regex_ext_var).pack(side="left")
        ttk.Checkbutton(opt_row, text="大小写敏感", variable=self.regex_case_var).pack(side="left", padx=(12, 0))

        # 2) 前缀 / 后缀
        f_affix = ttk.LabelFrame(rules_outer, text="2) 添加前缀 / 后缀")
        f_affix.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        f_affix.columnconfigure(1, weight=1)
        ttk.Label(f_affix, text="前缀：").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        ttk.Entry(f_affix, textvariable=self.prefix_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=6)
        ttk.Label(f_affix, text="后缀：").grid(row=1, column=0, sticky="e", padx=6, pady=6)
        ttk.Entry(f_affix, textvariable=self.suffix_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=6)
        ttk.Label(f_affix, text="（两者可只填其一；内容会参与非法字符与保留名校验）",
                  foreground="#555555").grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))

        # 3) 顺序编号
        f_num = ttk.LabelFrame(rules_outer, text="3) 顺序编号追加（勾选后在文件名末尾追加 _数字）")
        f_num.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        ttk.Checkbutton(f_num, text="启用顺序编号", variable=self.number_enabled_var).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=6, pady=(6, 2))
        ttk.Label(f_num, text="起始值：").grid(row=1, column=0, sticky="e", padx=(6, 2), pady=3)
        ttk.Entry(f_num, textvariable=self.start_var, width=8).grid(
            row=1, column=1, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(f_num, text="步长：").grid(row=1, column=2, sticky="e", padx=(6, 2), pady=3)
        ttk.Entry(f_num, textvariable=self.step_var, width=8).grid(
            row=1, column=3, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(f_num, text="补零位数：").grid(row=1, column=4, sticky="e", padx=(6, 2), pady=3)
        ttk.Entry(f_num, textvariable=self.pad_var, width=6).grid(
            row=1, column=5, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(f_num, text="排序依据：").grid(row=2, column=0, sticky="e", padx=(6, 2), pady=(3, 6))
        ttk.Combobox(f_num, textvariable=self.sort_var, values=list(SORT_LABELS),
                     state="readonly", width=12).grid(
            row=2, column=1, columnspan=2, sticky="w", padx=(0, 8), pady=(3, 6))
        ttk.Label(f_num, text="（仅决定编号分配顺序，不改变表格显示顺序）",
                  foreground="#555555").grid(row=3, column=0, columnspan=6, sticky="w", padx=6, pady=(0, 6))

        # 4) 时间插入
        f_time = ttk.LabelFrame(rules_outer, text="4) 时间插入")
        f_time.grid(row=1, column=1, sticky="nsew", padx=6, pady=6)
        f_time.columnconfigure(1, weight=1)
        ttk.Label(f_time, text="时间命名：").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        self.time_combo = ttk.Combobox(f_time, textvariable=self.time_mode_var,
                                       values=list(TIME_LABELS), state="readonly", width=14)
        self.time_combo.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(f_time, text="时间格式：").grid(row=1, column=0, sticky="e", padx=6, pady=6)
        ttk.Entry(f_time, textvariable=self.time_format_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=6)
        ttk.Label(f_time, text="（默认对应 YYYYMMDD_HHMMSS；也可直接写 strftime，如 %Y%m%d_%H%M%S）",
                  foreground="#555555").grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))
        ttk.Label(f_time, text="编号在前、时间在后，例如：原名_001_20240101_120000",
                  foreground="#555555").grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))

        # 5) 全局编号计数器（{num} 占位符专用）
        f_ph = ttk.LabelFrame(
            rules_outer,
            text="5) 全局编号计数器（{num} 占位符专用：只替换占位符，绝不自动追加数字或下划线）")
        f_ph.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=6, pady=(0, 6))
        ttk.Label(f_ph, text="起始值：").grid(row=0, column=0, sticky="e", padx=(6, 2), pady=6)
        ttk.Entry(f_ph, textvariable=self.ph_start_var, width=8).grid(
            row=0, column=1, sticky="w", padx=(0, 10), pady=6)
        ttk.Label(f_ph, text="步长：").grid(row=0, column=2, sticky="e", padx=(6, 2), pady=6)
        ttk.Entry(f_ph, textvariable=self.ph_step_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(0, 10), pady=6)
        ttk.Label(f_ph, text="补零位数：").grid(row=0, column=4, sticky="e", padx=(6, 2), pady=6)
        ttk.Entry(f_ph, textvariable=self.ph_pad_var, width=6).grid(
            row=0, column=5, sticky="w", padx=(0, 10), pady=6)
        ttk.Label(f_ph, text="排序依据：").grid(row=0, column=6, sticky="e", padx=(6, 2), pady=6)
        ttk.Combobox(f_ph, textvariable=self.ph_sort_var, values=list(SORT_LABELS),
                     state="readonly", width=12).grid(row=0, column=7, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(f_ph,
                  text="用法：在前缀 / 后缀 / 正则替换的“替换为”里写 {num}，无需勾选上面的顺序编号复选框。"
                       "例：正则填 ^.*$ 或 .*（不勾选“正则包含扩展名”）、替换为填 {num}，即可把主文件名整体换成编号，"
                       "得到 20.txt 这样的纯数字文件名；想写字面量 {num} 请用 {{num}}。",
                  foreground="#555555", wraplength=860, justify="left").grid(
            row=1, column=0, columnspan=8, sticky="w", padx=6, pady=(0, 6))

        # ③ 预览表格区 -------------------------------------------------------
        mid = ttk.LabelFrame(root, text="③ 预览表格（表格内全部文件都会被改名；多选仅用于目视对比）")
        mid.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        columns = ("old", "new", "path")
        self.tree = ttk.Treeview(mid, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("old", text="原文件名")
        self.tree.heading("new", text="预览后的新文件名")
        self.tree.heading("path", text="文件路径")
        self.tree.column("old", width=260, anchor="w", stretch=True)
        self.tree.column("new", width=320, anchor="w", stretch=True)
        self.tree.column("path", width=460, anchor="w", stretch=True)
        self.tree.tag_configure("err", background="#ffd2d2", foreground="#a40000")

        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(mid, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 6))
        hsb.grid(row=1, column=0, sticky="ew", padx=(6, 0))
        self._drop_targets = [top, mid, self.tree, root]

        # ④ 操作按钮区 + 状态栏 ---------------------------------------------
        bottom = ttk.Frame(root)
        bottom.grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 8))
        bottom.columnconfigure(0, weight=1)

        btns = ttk.Frame(bottom)
        btns.grid(row=0, column=0, sticky="e")
        self.undo_btn = ttk.Button(btns, text="撤销上次改名", command=self.on_undo, state="disabled")
        self.undo_btn.pack(side="right", padx=(8, 0))
        self.confirm_btn = ttk.Button(btns, text="确认执行改名", command=self.on_confirm, state="disabled")
        self.confirm_btn.pack(side="right")

        status_bar = ttk.Frame(bottom)
        status_bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        status_bar.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status_bar, textvariable=self.status_var, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.hint_label = ttk.Label(status_bar, textvariable=self.hint_var, anchor="e",
                                    foreground="#555555")
        self.hint_label.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _bind_events(self):
        # 任意规则参数变化 → 200ms 防抖后刷新预览
        for var in (self.regex_enabled_var, self.regex_var, self.regex_repl_var,
                    self.regex_ext_var, self.regex_case_var,
                    self.prefix_var, self.suffix_var,
                    self.number_enabled_var, self.start_var, self.step_var, self.pad_var,
                    self.sort_var,
                    self.ph_start_var, self.ph_step_var, self.ph_pad_var, self.ph_sort_var,
                    self.time_format_var):
            var.trace_add("write", self._on_rule_changed)

        # 后缀筛选变化 → 对表格内文件重新过滤（不重新扫描磁盘）
        self.filter_var.trace_add("write", self._on_rule_changed)

        self.time_combo.bind("<<ComboboxSelected>>", self._on_time_mode_change)

    def _on_rule_changed(self, *_args):
        self.schedule_preview()

    def _setup_dnd(self):
        if not DND_AVAILABLE:
            self.hint_var.set(DND_DISABLED_HINT)
            return
        registered = False
        for widget in getattr(self, "_drop_targets", []):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)
                registered = True
            except Exception:
                pass
        self.hint_var.set(DND_ENABLED_HINT if registered else DND_DISABLED_HINT)

    def _clear_tree(self):
        """清空 Treeview（逐条删除，避免空表时的参数问题）。"""
        for iid in self.tree.get_children():
            self.tree.delete(iid)

    # --------------------------------------------------------------- 状态栏
    def set_status(self, text: str, warn: bool = False):
        self.status_var.set(text)
        try:
            self.status_label.configure(foreground="#b00000" if warn else "#000000")
        except tk.TclError:
            pass

    def update_count(self):
        self.count_var.set("已添加文件：%d 个；当前表格（待处理）：%d 个"
                           % (len(self.files), len(self.rows)))

    def update_undo_button(self):
        self.undo_btn.configure(state="normal" if self.undo_record else "disabled")

    # --------------------------------------------------------- 预览与刷新
    def schedule_preview(self, *_args, delay: int = PREVIEW_DEBOUNCE_MS):
        if self._preview_job is not None:
            try:
                self.root.after_cancel(self._preview_job)
            except Exception:
                pass
        self._preview_job = self.root.after(delay, self.run_preview)

    def preview_now(self):
        if self._preview_job is not None:
            try:
                self.root.after_cancel(self._preview_job)
            except Exception:
                pass
            self._preview_job = None
        self.run_preview()

    def run_preview(self):
        self._preview_job = None
        parts = []
        if self._pending_msg:
            parts.append(self._pending_msg)
            self._pending_msg = ""

        # 按当前后缀筛选条件，对“已经添加的文件”重新过滤（不重新扫描磁盘）
        filters = parse_suffix_filter(self.filter_var.get())
        self.rows = [f for f in self.files if name_matches_filter(f.name, filters)]
        self.update_count()

        warn = False
        if len(self.rows) > MANY_FILES_THRESHOLD:
            parts.append(MANY_FILES_HINT)

        self._clear_tree()
        self._results = []
        self._preview_map = {}
        self._preview_paths = {}

        if not self.rows:
            self._can_confirm = False
            self.confirm_btn.configure(state="disabled")
            parts.append(READY_HINT if not self.files else "当前筛选条件下没有文件")
            self.set_status("；".join(parts), False)
            return

        rules, errors = self.collect_rules()
        if errors:
            # 正则表达式无效 / 编号参数非法：预览全部清空，确认按钮禁用
            for e in self.rows:
                self.tree.insert("", "end", iid=str(id(e)), values=(e.name, "", e.path))
            self._can_confirm = False
            self.confirm_btn.configure(state="disabled")
            parts.append("；".join(errors))
            self.set_status("；".join(parts), True)
            return

        results = compute_preview(self.rows, rules)
        if results is None:  # 兜底：正则无效
            for e in self.rows:
                self.tree.insert("", "end", iid=str(id(e)), values=(e.name, "", e.path))
            self._can_confirm = False
            self.confirm_btn.configure(state="disabled")
            parts.append(REGEX_ERROR_HINT)
            self.set_status("；".join(parts), True)
            return

        self._results = results
        for e, _new in results:
            self._preview_paths[id(e)] = e.path

        problems, _groups = validate_results(results, self.rows)
        problem_rows = len(problems)
        all_same = True
        for e, new_name in results:
            codes = problems.get(id(e), set())
            if codes:
                warn = True
            if new_name != e.name:
                all_same = False
            self.tree.insert("", "end", iid=str(id(e)),
                             values=(e.name, new_name, e.path),
                             tags=("err",) if codes else ())
            self._preview_map[id(e)] = new_name

        if rules.ph_active:
            parts.append(PH_ACTIVE_HINT)

        if problem_rows:
            parts.append("检测到 %d 行存在问题（红色高亮），确认改名已禁用" % problem_rows)
            self._can_confirm = False
            self.confirm_btn.configure(state="disabled")
            warn = True
        elif all_same:
            parts.append("新文件名与原始文件名完全一致，确认改名已禁用")
            self._can_confirm = False
            self.confirm_btn.configure(state="disabled")
        else:
            parts.append("预览完成：共 %d 个文件，可执行改名" % len(results))
            self._can_confirm = True
            self.confirm_btn.configure(state="normal")

        self.set_status("；".join(parts), warn)

    def collect_rules(self):
        """从界面读取规则；返回 (Rules, errors)。"""
        errors = []
        rules = Rules()

        rules.regex_enabled = bool(self.regex_enabled_var.get())
        rules.regex = self.regex_var.get()
        rules.regex_repl = self.regex_repl_var.get()
        rules.regex_include_ext = bool(self.regex_ext_var.get())
        rules.regex_case_sensitive = bool(self.regex_case_var.get())
        if rules.regex_enabled:
            flags = 0 if rules.regex_case_sensitive else re.IGNORECASE
            try:
                re.compile(rules.regex, flags)
            except re.error:
                errors.append(REGEX_ERROR_HINT)

        rules.prefix = self.prefix_var.get()
        rules.suffix = self.suffix_var.get()

        rules.number_enabled = bool(self.number_enabled_var.get())
        if rules.number_enabled:
            start = parse_int_strict(self.start_var.get())
            step = parse_int_strict(self.step_var.get())
            pad = parse_int_strict(self.pad_var.get())
            if start is None or step is None:
                errors.append("起始值、步长必须为整数（可负数）")
            if pad is None or not (1 <= pad <= 10):
                errors.append("补零位数必须是 1-10 之间的整数")
            rules.start = start if start is not None else 0
            rules.step = step if step is not None else 1
            rules.pad = pad if (pad is not None and 1 <= pad <= 10) else 3

        rules.sort_key = SORT_KEYS.get(self.sort_var.get(), "table")

        # 独立的全局编号计数器：与“启用顺序编号”复选框无关，始终可解析 {num}
        rules.ph_sort_key = SORT_KEYS.get(self.ph_sort_var.get(), "table")
        ph_texts = [self.prefix_var.get(), self.suffix_var.get()]
        if rules.regex_enabled:
            ph_texts.append(self.regex_repl_var.get())
        rules.ph_active = num_token_used(*ph_texts)
        ph_start = parse_int_strict(self.ph_start_var.get())
        ph_step = parse_int_strict(self.ph_step_var.get())
        ph_pad = parse_int_strict(self.ph_pad_var.get())
        if rules.ph_active:
            # 只有真正用到 {num} 时才校验这套计数器，避免影响不使用占位符的用户
            if ph_start is None or ph_step is None:
                errors.append("全局计数器（{num}）：起始值、步长必须为整数（可负数）")
            if ph_pad is None or not (1 <= ph_pad <= 10):
                errors.append("全局计数器（{num}）：补零位数必须是 1-10 之间的整数")
        rules.ph_start = ph_start if ph_start is not None else 1
        rules.ph_step = ph_step if ph_step is not None else 1
        rules.ph_pad = ph_pad if (ph_pad is not None and 1 <= ph_pad <= 10) else 1

        rules.time_mode = TIME_KEYS.get(self.time_mode_var.get(), "none")
        fmt = self.time_format_var.get().strip()
        rules.time_format = fmt if fmt else DEFAULT_TIME_FORMAT
        return rules, errors

    def _on_time_mode_change(self, _event=None):
        if self.time_mode_var.get() == TIME_LABELS[2] and not creation_time_supported() \
                and not self._ctime_warned:
            # 仅第一次切到“创建时间”且无法获取时提示一次
            self._ctime_warned = True
            messagebox.showinfo("提示", "当前系统无法获取文件创建时间，将自动降级使用修改时间。")
        self.schedule_preview()

    # ------------------------------------------------------- 文件添加/筛选
    def choose_folder(self):
        folder = filedialog.askdirectory(title="选择要扫描的文件夹")
        if folder:
            self.add_paths([folder])

    def choose_files(self):
        paths = filedialog.askopenfilenames(title="选择文件")
        if paths:
            self.add_paths(list(paths))

    def clear_files(self):
        self.files = []
        self.rows = []
        self._index = {}
        self._order_counter = 0
        self._clear_tree()
        self._pending_msg = "已清空列表"
        self.preview_now()

    def add_paths(self, paths):
        """统一入口：支持文件与文件夹（文件夹按“包含子文件夹”复选框决定递归）。"""
        recursive = bool(self.recursive_var.get())
        filters = parse_suffix_filter(self.filter_var.get())
        added = skipped_dup = skipped_filter = 0

        for raw in paths:
            if not raw:
                continue
            try:
                p = os.path.abspath(raw)
            except Exception:
                continue
            if os.path.isdir(p):
                for f in collect_files_from_dir(p, recursive):
                    state = self._try_add_file(f, filters)
                    if state == "add":
                        added += 1
                    elif state == "dup":
                        skipped_dup += 1
                    else:
                        skipped_filter += 1
            elif os.path.isfile(p):
                state = self._try_add_file(p, filters)
                if state == "add":
                    added += 1
                elif state == "dup":
                    skipped_dup += 1
                else:
                    skipped_filter += 1
            # 其它类型（目录符号链接等非文件）直接忽略：本工具只处理文件

        msg = "新增 %d 个文件" % added
        if skipped_dup:
            msg += "，跳过重复 %d 个" % skipped_dup
        if skipped_filter:
            msg += "，不符合后缀筛选 %d 个" % skipped_filter
        self._pending_msg = msg
        self.schedule_preview()

    def _try_add_file(self, path: str, filters):
        """按绝对路径去重 + 当前后缀筛选；返回 'add' / 'dup' / 'filter'。"""
        name = os.path.basename(path)
        if not name:
            return "filter"
        if not name_matches_filter(name, filters):
            return "filter"
        key = norm_key(path)
        if key in self._index:
            return "dup"
        entry = FileEntry(os.path.abspath(path), self._order_counter)
        self._order_counter += 1
        self.files.append(entry)
        self._index[key] = entry
        return "add"

    def reindex(self):
        self._index = {}
        for e in self.files:
            self._index[norm_key(e.path)] = e

    # ------------------------------------------------------------ 拖拽支持
    def on_drop(self, event):
        if not DND_AVAILABLE:
            self.set_status(DND_DISABLED_HINT, True)
            return
        try:
            raw = self.root.tk.splitlist(event.data)
        except Exception:
            raw = [event.data]
        self.add_paths(list(raw))

    # ------------------------------------------------------------ 执行改名
    def on_confirm(self):
        if not self.rows:
            return
        rules, errors = self.collect_rules()
        if errors:
            self.preview_now()
            messagebox.showerror("参数错误", "；".join(errors))
            return

        results = compute_preview(self.rows, rules)
        if results is None:
            self.preview_now()
            messagebox.showerror("参数错误", REGEX_ERROR_HINT)
            return

        problems, _groups = validate_results(results, self.rows)
        if problems:
            self.preview_now()
            messagebox.showerror("存在校验问题",
                                 "预览中有 %d 行存在问题（红色高亮），请修正规则后再执行。" % len(problems))
            return

        ops = [(e, new) for e, new in results if new != e.name]
        if not ops:
            self.preview_now()
            return

        if not messagebox.askyesno("确认执行改名",
                                   "一共将要修改 %d 个文件。\n\n是否继续？" % len(ops)):
            self.set_status("已取消本次改名")
            return

        # 执行前重新校验：只检查文件是否存在、绝对路径是否与预览阶段一致
        stale = []
        for e, _new in ops:
            snapshot = self._preview_paths.get(id(e))
            if not os.path.isfile(e.path):
                stale.append("%s  →  文件已不存在" % e.path)
                continue
            if snapshot != e.path or norm_key(e.path) != os.path.normcase(e.path):
                stale.append("%s  →  路径与预览阶段不一致" % e.path)
        if stale:
            detail = "\n".join(stale[:50])
            if len(stale) > 50:
                detail += "\n...（其余 %d 项略）" % (len(stale) - 50)
            messagebox.showerror("无法执行改名",
                                 "以下文件已被删除或路径发生变化，本次改名已放弃，请重新预览：\n\n" + detail)
            self.preview_now()
            self.set_status("待处理文件已变化，本次改名已放弃", True)
            return

        self.do_rename(ops)

    def do_rename(self, ops):
        ops = order_rename_ops(ops)
        total = len(ops)
        skipped = max(0, len(self.rows) - total)
        done = []
        failure = None

        for index, (entry, new_name) in enumerate(ops, 1):
            old_path = entry.path
            new_path = os.path.join(entry.dir, new_name)
            try:
                os.rename(old_path, new_path)
            except OSError as exc:
                failure = (entry, new_name, exc)
                break
            done.append((entry, old_path, new_path))
            if index % PROGRESS_STEP == 0 or index == total:
                self.set_status("正在改名... %d/%d" % (index, total))
                try:
                    self.root.update_idletasks()
                except tk.TclError:
                    pass

        if failure is not None:
            # 改名事务：任意失败即回滚；回滚本身也可能失败，失败清单弹窗通知
            rollback_failed = []
            for entry, old_path, new_path in reversed(done):
                try:
                    os.rename(new_path, old_path)
                except OSError as exc:
                    rollback_failed.append("%s  →  %s（%s）" % (new_path, old_path, exc))
            entry, new_name, exc = failure
            msg = ("改名失败，已中止并回滚。\n\n失败文件：%s\n新名字：%s\n原因：%s\n\n"
                   "已成功回滚 %d/%d 个文件。" % (entry.path, new_name, exc,
                                            len(done) - len(rollback_failed), len(done)))
            if rollback_failed:
                msg += "\n\n以下文件回滚失败，请手动处理：\n" + "\n".join(rollback_failed[:50])
                if len(rollback_failed) > 50:
                    msg += "\n...（其余 %d 项略）" % (len(rollback_failed) - 50)
            messagebox.showerror("改名失败", msg)
            self.set_status("改名失败，已回滚，未生成撤销记录", True)
            # 条目未做修改，预览重新计算即可
            self.preview_now()
            return

        # 全部成功：更新表格数据、写入撤销记录
        for entry, old_path, new_path in done:
            entry.path = new_path
            entry.dir = os.path.dirname(new_path)
            entry.name = os.path.basename(new_path)
            entry.refresh_stat()
        self.reindex()

        self.undo_record = list(done)
        self.update_undo_button()

        msg = "改名完成，成功 %d 个文件" % len(done)
        if skipped:
            msg += "（跳过 %d 个未变化文件）" % skipped
        self._pending_msg = msg
        self.preview_now()

    # -------------------------------------------------------------- 撤销
    def on_undo(self):
        if not self.undo_record:
            return
        record = list(self.undo_record)
        failures = []
        restored = 0

        for entry, old_path, new_path in reversed(record):
            if not os.path.isfile(new_path):
                failures.append("%s  →  文件已被删除或移动，无法撤销" % new_path)
                continue
            if os.path.exists(old_path):
                failures.append("%s  →  原文件名已被占用，无法撤销" % old_path)
                continue
            try:
                os.rename(new_path, old_path)
            except OSError as exc:
                failures.append("%s  →  %s（%s）" % (new_path, old_path, exc))
                continue
            entry.path = old_path
            entry.dir = os.path.dirname(old_path)
            entry.name = os.path.basename(old_path)
            entry.refresh_stat()
            restored += 1

        # 撤销后立刻清空撤销记录，按钮置灰；表格刷新，新文件名列按当前规则重算
        self.undo_record = []
        self.update_undo_button()
        self.reindex()

        if failures:
            detail = "\n".join(failures[:50])
            if len(failures) > 50:
                detail += "\n...（其余 %d 项略）" % (len(failures) - 50)
            messagebox.showwarning("撤销未完全成功",
                                   "已撤销 %d 个文件，以下 %d 个文件撤销失败：\n\n%s"
                                   % (restored, len(failures), detail))
            self._pending_msg = "撤销完成 %d 个，失败 %d 个" % (restored, len(failures))
        else:
            self._pending_msg = "撤销完成，已恢复 %d 个文件的原文件名" % restored

        self.preview_now()


def main():
    root = None
    if DND_AVAILABLE:
        try:
            root = TkinterDnD.Tk()
        except Exception:
            root = None
    if root is None:
        root = tk.Tk()
    RenameToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
