#!/usr/bin/env python3
import ast
import base64
import builtins
import copy
import functools
import hashlib
import keyword
import marshal
import math
import os
import random
import sys
import subprocess
import tempfile
import time
import tracemalloc
import traceback
import unicodedata
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Union, Callable, Tuple
REQUIRED_PYTHON_VERSION = (3, 12)
def _check_tool_python_version(version_info=sys.version_info) -> None:
    if tuple(version_info[:2]) != REQUIRED_PYTHON_VERSION:
        raise SystemExit(
            f"DkhObfuscate requires Python "
            f"{REQUIRED_PYTHON_VERSION[0]}.{REQUIRED_PYTHON_VERSION[1]} "
            f"(running {version_info[0]}.{version_info[1]}). "
            f"Use Python {REQUIRED_PYTHON_VERSION[0]}.{REQUIRED_PYTHON_VERSION[1]}.")
_check_tool_python_version()
PIPELINE_SCHEMA_VERSION = 2
MAPPING_SCHEMA_VERSION = 1
PAYLOAD_FORMAT_VERSION = 2
INTEGRITY_SCHEMA_VERSION = 2
class DkhError(Exception):
    CODE = "ZD-E000"
    def __init__(self, message: str = ""):
        super().__init__(f"[{self.CODE}] {message}" if message else self.CODE)
class DkhParseError(DkhError):
    CODE = "ZD-E001"
class DkhTransformError(DkhError):
    CODE = "ZD-E002"
class DkhCompileError(DkhError):
    CODE = "ZD-E003"
class DkhRuntimeError(DkhError):
    CODE = "ZD-E004"
class DkhSizeGovernorError(DkhError):
    CODE = "ZD-E005"
    def __init__(self, message: str, report: Optional["BuildReport"] = None):
        super().__init__(message)
        self.report = report
class DkhInputLimitError(DkhError, ValueError):
    CODE = "ZD-E006"
class DkhRetaliationRefusal(DkhError):
    CODE = "ZD-E007"
def _seeded_rng(build_seed: int, purpose: str) -> random.Random:
    digest = hashlib.sha256(f"{build_seed}::{purpose}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))
def derive_seed(build_seed: int, namespace: str) -> int:
    d = hashlib.sha256(f"{build_seed}::ns::{namespace}".encode()).digest()
    return int.from_bytes(d[:8], "big")
def _hira_token(build_seed: int, purpose: str, length: int = 8) -> str:
    rng = _seeded_rng(build_seed, f"hira_token::{purpose}")
    return "".join(rng.choice(HIRAGANA_POOL) for _ in range(length))
def _cjk_lambda_arg(build_seed: int, purpose: str, index: int) -> str:
    kd = hashlib.sha256(
        f"{build_seed}::cjkarg::{purpose}::{index}".encode()).digest()
    ch = HIRAGANA_POOL[kd[0] % len(HIRAGANA_POOL)]
    return f"{ch}{index}"
def _hidden_char_expr(arg: str, k1: int, k2: int, value: int,
                       shape_rng: "random.Random") -> str:
    form = shape_rng.randrange(4)
    if form == 1:
        return f"(lambda {arg}: ({arg} + {k1} - {k2}) % 256)({(value - k1 + k2) % 256})"
    if form == 2:
        return f"(lambda {arg}: ({arg} - {k1} + {k2}) % 256)({(value + k1 - k2) % 256})"
    if form == 3:
        _k1 = (k1 | 1) & 255 or 1
        _inv = pow(_k1, -1, 256)
        return (f"(lambda {arg}: ({arg} * {_k1} + {k2}) % 256)"
                f"({((value - k2) * _inv) % 256})")
    return f"(lambda {arg}: {arg} ^ ({k1}^{k2}))({value ^ k1 ^ k2})"
class SeedBookkeeper:
    NAMESPACES = ("naming", "semantic", "helpers", "vm", "cfg",
                  "literals", "payload", "integrity", "expansion")
    def __init__(self, build_seed: int):
        self.build_seed = build_seed
        self._seeds = {ns: derive_seed(build_seed, ns)
                       for ns in self.NAMESPACES}
    def __getitem__(self, ns: str) -> int:
        return self._seeds[ns]
    def as_dict(self) -> Dict[str, int]:
        return dict(self._seeds)
PROFILES: Dict[str, Dict[str, Any]] = {
    "SAFE": dict(
        enable_vm=True, enable_structural_expansion=True,
        expansion_level="light", ast_depth=2,
        enable_memory_error_dispatch=False, enable_opaque_predicates=False,
        enable_boolean_algebra=True, enable_builtins_indirection=False,
        more_obfuscation=True, enable_anti_decompli=True,
        anti_combo=True, enable_runtime_guard=False,
        requests_protect=True, enable_tamper_response=True,
        enable_integrity=True, identifier_style="ascii",
        enable_int_pool=False,
    ),
    "BALANCED": dict(
        enable_vm=True, enable_structural_expansion=True,
        expansion_level="high", ast_depth=5,
        enable_memory_error_dispatch=True, enable_opaque_predicates=True,
        enable_boolean_algebra=True, enable_builtins_indirection=False,
        more_obfuscation=True, enable_anti_decompli=True,
        anti_combo=True, enable_runtime_guard=True,
        requests_protect=True, enable_tamper_response=True,
        enable_integrity=True,         identifier_style="hiragana",
        enable_int_pool=True,
        max_expansion_nodes=200_000, max_build_retries=2,
        max_boost_rounds=8, max_escalation_rebuilds=3,
    ),
    "HARD": dict(
        enable_vm=True, enable_structural_expansion=True,
        expansion_level="high", ast_depth=6,
        enable_memory_error_dispatch=True, enable_opaque_predicates=True,
        enable_boolean_algebra=True, enable_builtins_indirection=True,
        more_obfuscation=True, enable_anti_decompli=True,
        anti_combo=True, enable_runtime_guard=True,
        requests_protect=True, enable_tamper_response=True,
        enable_integrity=True,         identifier_style="hiragana",
        enable_int_pool=True,
        enable_lambda_thunk=True,
        max_expansion_nodes=400_000, max_build_retries=3,
        max_boost_rounds=10, max_escalation_rebuilds=4,
    ),
    "MAX": dict(
        enable_vm=True, enable_structural_expansion=True,
        expansion_level="extreme", ast_depth=8,
        enable_memory_error_dispatch=True, enable_opaque_predicates=True,
        enable_boolean_algebra=True, enable_builtins_indirection=True,
        more_obfuscation=True, enable_anti_decompli=True,
        anti_combo=True, enable_runtime_guard=True,
        requests_protect=True, enable_tamper_response=True,
        enable_integrity=True, identifier_style="auto",
        enable_int_pool=True,
        enable_lambda_thunk=True,
        max_expansion_nodes=600_000, max_build_retries=3,
        max_boost_rounds=10, max_escalation_rebuilds=5,
    ),
    "DKH": dict(
        enable_vm=True, enable_structural_expansion=True,
        expansion_level="high", ast_depth=5,
        enable_memory_error_dispatch=True, enable_opaque_predicates=True,
        enable_boolean_algebra=True, enable_builtins_indirection=True,
        enable_lambda_thunk=True,
        more_obfuscation=True, enable_anti_decompli=True,
        anti_combo=True, enable_runtime_guard=True,
        requests_protect=True, enable_tamper_response=True,
        enable_integrity=True,         identifier_style="hiragana",
        enable_int_pool=True,
        enable_stream_decrypt=True,
        expansion_profile="DKH",
        max_expansion_nodes=200_000, max_build_retries=2,
        max_boost_rounds=8, max_escalation_rebuilds=3,
    ),
    "APEX": dict(
        enable_vm=True, enable_structural_expansion=True,
        expansion_level="extreme", ast_depth=8,
        enable_memory_error_dispatch=True, enable_opaque_predicates=True,
        enable_boolean_algebra=True, enable_builtins_indirection=True,
        more_obfuscation=True, enable_anti_decompli=True,
        anti_combo=True, enable_runtime_guard=True,
        requests_protect=True, enable_tamper_response=True,
        enable_integrity=True, identifier_style="auto",
        enable_int_pool=True,
        enable_lambda_thunk=True,
        enable_globals_storage=True,
        enable_chain_links=True, enable_handler_embed=True,
        enable_dead_bloat=True, dead_bloat_ratio=0.6,
        enable_int_hide=True, enable_decompiler_traps=True,
        retaliation_scan="refuse",
        expansion_profile="DKH",
        max_input_bytes=0, max_output_bytes=0, max_build_seconds=0.0,
        max_expansion_nodes=600_000, max_build_retries=3,
        max_boost_rounds=10, max_escalation_rebuilds=5,
    ),
}
def normalize_profile_name(name: Optional[str]) -> str:
    if not name:
        return "CUSTOM"
    n = str(name).strip().upper()
    return n if n in PROFILES or n == "CUSTOM" else "CUSTOM"
@dataclass
class DragonConfig:
    enable_identifier_mangling: bool = True
    enable_string_protection: bool = True
    enable_constant_protection: bool = True
    enable_structural_expansion: bool = True
    enable_control_flow: bool = True
    enable_vm: bool = True
    enable_integrity: bool = True
    identifier_prefix: str = "_zy"
    identifier_style: str = "cjk"
    rename_functions: bool = True
    rename_classes: bool = True
    rename_locals: bool = True
    rename_arguments: bool = True
    rename_module_level: bool = True
    script_mode: bool = True
    ast_depth: int = 3
    expansion_level: str = "light"
    max_expansion_nodes: int = 500
    def _effective_max_nodes(self) -> int:
        budgets = {
            "minimal": 200,
            "light":   1_000,
            "high":    8_000,
            "extreme": 30_000,
        }
        return budgets.get(self.expansion_level, self.max_expansion_nodes)
    enable_globals_storage: bool = True
    enable_user_payload_arch: bool = True
    enable_marshal_runtime: bool = True
    enable_runtime_guard: bool = True
    enable_memory_error_dispatch: bool = True
    enable_builtins_indirection: bool = True
    enable_opaque_predicates: bool = True
    enable_int_pool: bool = True
    enable_int_hide: bool = True
    enable_chain_links: bool = True
    enable_handler_embed: bool = True
    enable_dead_bloat: bool = True
    dead_bloat_ratio: float = 0.30
    enable_decompiler_traps: bool = True
    enable_match_flatten: bool = True
    enable_from_import_hide: bool = True
    enable_requests_forge: bool = True
    enable_residual_vault: bool = True
    enable_module_maze: bool = True
    retaliation_scan: str = "refuse"
    enable_lambda_thunk: bool = True
    enable_boolean_algebra: bool = True
    more_obfuscation: bool = True
    enable_stream_decrypt: bool = True
    anti_combo: bool = True
    anti_debug: bool = True
    anti_hook: bool = True
    anti_dump: bool = True
    requests_protect: bool = True
    enable_tamper_response: bool = True
    tamper_url: str = "https://dkhang.pages.dev"
    enable_anti_decompli: bool = True
    max_input_bytes: int = 0
    band_headroom_fixed: int = 53248
    band_headroom_ratio: float = 7.5
    min_output_bytes: int = 250 * 1024
    max_output_bytes: int = 0
    max_build_retries: int = 3
    max_boost_rounds: int = 10
    max_escalation_rebuilds: int = 5
    max_build_seconds: float = 0.0
    enable_per_round_size_tracking: bool = True
    payload_compression_level: int = 9
    seed: Optional[int] = None
    profile: str = "CUSTOM"
    expansion_profile: str = "DKH"
    audit_mapping: bool = False
    enforce_ibe_band: bool = True
    def apply_profile(self, name: Optional[str] = None) -> "DragonConfig":
        pname = normalize_profile_name(name or self.profile)
        self.profile = pname
        for k, v in PROFILES.get(pname, {}).items():
            setattr(self, k, v)
        return self
    username: str = "Username"
    target_python_version: Tuple[int, int] = (3, 12)
    emit_mapping_file: bool = True
    warnings: list = field(default_factory=list)
    def log_warning(self, module: str, message: str) -> None:
        self.warnings.append(f"[{module}] {message}")
    def log_skip(self, module: str, reason: str) -> None:
        self.warnings.append(f"[SKIPPED] {module}: {reason}")
@dataclass
class SizeProfile:
    name: str
    node_multiplier: float
    max_rounds: int
    med_cap: int
    junk_cases: int
    global_med_cap_per_kb: float = 12.0
_SIZE_PROFILES: List[Tuple[int, SizeProfile]] = [
    (2_000,     SizeProfile("tiny",   45.0, 3, 6, 3, 24.0)),
    (20_000,    SizeProfile("small",  25.0, 3, 5, 2, 16.0)),
    (100_000,   SizeProfile("medium", 13.0, 2, 4, 2, 9.0)),
    (300_000,   SizeProfile("large",   7.0, 2, 3, 1, 6.0)),
    (500_001,   SizeProfile("xlarge",  4.0, 1, 2, 1, 3.5)),
]
def _fmt_ibe_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    if num_bytes % 1024 == 0:
        return f"{num_bytes // 1024} KB"
    return f"{num_bytes / 1024:.1f} KB"
def normalize_expansion_profile(name: Optional[str]) -> str:
    return "DKH"
_DKH_BASE_IN = 10 * 1024
_DKH_BASE_OUT_LO = 450 * 1024
_DKH_BASE_OUT_HI = 500 * 1024
_DKH_STEP_IN = 20 * 1024
_DKH_STEP_OUT_LO = 50 * 1024
_DKH_STEP_OUT_HI = 80 * 1024
def _dkh_band_for_input(input_bytes: int) -> Tuple[int, int]:
    if input_bytes < 1:
        raise ValueError("Dkh requires at least 1 input byte")
    if input_bytes < _DKH_BASE_IN:
        return _DKH_BASE_OUT_LO, _DKH_BASE_OUT_HI
    steps = (input_bytes - _DKH_BASE_IN) // _DKH_STEP_IN
    return (_DKH_BASE_OUT_LO + steps * _DKH_STEP_OUT_LO,
            _DKH_BASE_OUT_HI + steps * _DKH_STEP_OUT_HI)
def _dkh_band_label(input_bytes: int) -> str:
    out_lo, out_hi = _dkh_band_for_input(input_bytes)
    return f"{_fmt_ibe_size(out_lo)}..{_fmt_ibe_size(out_hi)}"
def _max_input_bytes_for(config: "DragonConfig") -> int:
    return int(getattr(config, "max_input_bytes", 0) or 0)
def _band_structural_limit(config: "DragonConfig",
                           adaptive: "AdaptiveExpansionController") -> int:
    try:
        ceiling = int(adaptive.output_ceiling or 0)
    except Exception:
        ceiling = 0
    fixed = int(getattr(config, "band_headroom_fixed", 53248) or 0)
    ratio = float(getattr(config, "band_headroom_ratio", 7.5) or 7.5)
    if ratio <= 0:
        ratio = 7.5
    try:
        comp = int(getattr(config, "payload_compression_level", 9) or 9)
    except Exception:
        comp = 9
    if comp < 6:
        ratio = ratio * 1.2
    if ceiling <= 0:
        try:
            _ib = int(adaptive.input_bytes or 0)
        except Exception:
            _ib = 0
        return max(8192, _ib * 8)
    return max(8192, int((ceiling - fixed) / ratio))
class AdaptiveExpansionController:
    def __init__(self, input_bytes: int, input_ast_nodes: int,
                 input_stmts: int, config: "DragonConfig",
                 attempt: int = 0, escalation: int = 0):
        self.input_bytes = max(1, input_bytes)
        self.input_ast_nodes = max(1, input_ast_nodes)
        self.input_stmts = max(1, input_stmts)
        self.config = config
        self.profile = self._select_profile()
        self.size_profile = self.profile.name
        self.max_rounds = self.profile.max_rounds
        self.med_cap = self.profile.med_cap
        self.junk_cases = self.profile.junk_cases
        self.global_med_cap = max(20, int(
            (self.input_bytes / 1000.0) * self.profile.global_med_cap_per_kb
        ))
        self.expansion_profile = normalize_expansion_profile(
            getattr(config, "expansion_profile", "DKH"))
        if getattr(config, "enforce_ibe_band", True):
            _band_lo, _band_hi = _dkh_band_for_input(self.input_bytes)
            _mob = int(getattr(config, "max_output_bytes", 0) or 0)
            self.output_floor = max(_band_lo, config.min_output_bytes)
            self.output_ceiling = max(self.output_floor + 1, _band_hi)
            if _mob > 0:
                self.output_ceiling = min(self.output_ceiling, _mob)
        else:
            self.output_floor = 0
            _mob = int(getattr(config, "max_output_bytes", 0) or 0)
            if _mob > 0:
                self.output_ceiling = _mob * 4
            else:
                _band_lo, _band_hi = _dkh_band_for_input(self.input_bytes)
                self.output_ceiling = max(self.output_floor + 1, _band_hi)
        self.band_label = _dkh_band_label(self.input_bytes)
        _AVG_NODE_BYTES = 7.5
        self.target_body_floor_bytes = int(self.output_floor * 1.30) + 2048
        floor_needed_nodes = int(self.target_body_floor_bytes / _AVG_NODE_BYTES)
        raw_node_budget = int(self.input_ast_nodes * self.profile.node_multiplier)
        self.max_ast_nodes = min(
            max(raw_node_budget, min(floor_needed_nodes, 600_000)), 600_000)
        self.coverage = 0.55
        import math as _math
        if self.max_rounds < 24 and self.input_ast_nodes > 0:
            growth_needed = floor_needed_nodes / max(1, self.input_ast_nodes)
            if growth_needed > 1:
                compound_rounds = int(_math.ceil(
                    _math.log(growth_needed) / _math.log(1.6)))
                self.max_rounds = max(self.max_rounds,
                                      min(24, compound_rounds + 1))
        self.second_stage_rounds = max(1, self.max_rounds // 2)
        if config.more_obfuscation:
            self.second_stage_rounds = self.max_rounds
        self.attempt = max(0, int(attempt))
        if self.attempt >= 1:
            self.max_rounds = 1
            self.second_stage_rounds = 0
            self.med_cap = max(1, self.med_cap // 2)
            self.junk_cases = max(1, self.junk_cases // 2)
            self.global_med_cap = max(8, self.global_med_cap // 2)
            self.coverage = 0.35
            self.max_ast_nodes = min(
                int(self.input_ast_nodes * self.profile.node_multiplier
                    * 0.45), self.max_ast_nodes)
        if self.attempt >= 2:
            self.med_cap = 1
            self.junk_cases = 1
            self.global_med_cap = max(6, self.global_med_cap // 2)
            self.second_stage_rounds = 0
            self.coverage = 0.25
            self.max_ast_nodes = min(self.max_ast_nodes // 2, 400_000)
        if self.attempt >= 3:
            self.max_rounds = 0
            self.med_cap = 0
            self.second_stage_rounds = 0
            self.coverage = 0.15
            self.max_ast_nodes = 50_000
        self.escalation = max(0, int(escalation))
        if self.escalation >= 1 and self.attempt == 0:
            growth = 2.2 ** self.escalation
            self.max_ast_nodes = min(int(self.max_ast_nodes * growth), 600_000)
            self.max_rounds = min(10, self.max_rounds + self.escalation)
            self.second_stage_rounds = min(10, self.second_stage_rounds + self.escalation)
            self.med_cap = min(12, self.med_cap + 2 * self.escalation)
            self.junk_cases = min(6, self.junk_cases + self.escalation)
            self.global_med_cap = min(4000, int(self.global_med_cap * (1.5 ** self.escalation)))
            self.coverage = min(0.95, self.coverage + 0.18 * self.escalation)
    def _select_profile(self) -> SizeProfile:
        for threshold, profile in _SIZE_PROFILES:
            if self.input_bytes < threshold:
                return profile
        return _SIZE_PROFILES[-1][1]
    def describe(self) -> str:
        return (f"profile={self.size_profile} "
                f"exp={self.expansion_profile} "
                f"ibe_band={self.band_label} "
                f"band=[{_format_size(self.output_floor)}.."
                f"{_format_size(self.output_ceiling)}] "
                f"node_budget={self.max_ast_nodes} rounds={self.max_rounds} "
                f"coverage={self.coverage:.2f} "
                f"attempt={self.attempt} esc={self.escalation}")
DYNAMIC_ACCESS_FUNCS = {
    "getattr", "setattr", "hasattr", "delattr",
    "globals", "locals", "vars", "eval", "exec", "__import__",
}
DUNDER_PREFIX_SUFFIX = "__"
BUILTIN_NAMES = set(dir(builtins))
@dataclass
class ScopeInfo:
    kind: str
    name: str
    node: ast.AST
    parent: Optional["ScopeInfo"]
    bound_names: Set[str] = field(default_factory=set)
    children: List["ScopeInfo"] = field(default_factory=list)
@dataclass
class ScopeReport:
    module_scope: ScopeInfo
    global_unsafe_names: Set[str] = field(default_factory=set)
    declared_globals: Set[str] = field(default_factory=set)
    global_rename_map: Dict[str, str] = field(default_factory=dict)
    node_scope: Dict[int, ScopeInfo] = field(default_factory=dict)
    dynamic_referenced_names: Set[str] = field(default_factory=set)
    imported_names: Set[str] = field(default_factory=set)
    attribute_names: Set[str] = field(default_factory=set)
    warnings: List[str] = field(default_factory=list)
def is_dunder(name: str) -> bool:
    return name.startswith(DUNDER_PREFIX_SUFFIX) and name.endswith(DUNDER_PREFIX_SUFFIX)
class _DynamicAccessVisitor(ast.NodeVisitor):
    def __init__(self):
        self.dynamic_names: Set[str] = set()
        self.uses_eval_or_exec = False
    def visit_Call(self, node: ast.Call):
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name in DYNAMIC_ACCESS_FUNCS:
            if func_name in ("eval", "exec"):
                self.uses_eval_or_exec = True
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self.dynamic_names.add(arg.value)
        self.generic_visit(node)
class _KeywordArgCollector(ast.NodeVisitor):
    def __init__(self):
        self.keyword_arg_names: Set[str] = set()
    def visit_Call(self, node: ast.Call):
        for kw in node.keywords:
            if kw.arg is not None:
                self.keyword_arg_names.add(kw.arg)
        self.generic_visit(node)
class _AttributeCollector(ast.NodeVisitor):
    def __init__(self):
        self.attribute_names: Set[str] = set()
    def visit_Attribute(self, node: ast.Attribute):
        self.attribute_names.add(node.attr)
        self.generic_visit(node)
class _ScopeBuilder(ast.NodeVisitor):
    def __init__(self, report: ScopeReport):
        self.report = report
        self.scope_stack: List[ScopeInfo] = [report.module_scope]
    def _current(self) -> ScopeInfo:
        return self.scope_stack[-1]
    def _push(self, kind: str, name: str, node: ast.AST) -> ScopeInfo:
        scope = ScopeInfo(kind=kind, name=name, node=node, parent=self._current())
        self._current().children.append(scope)
        self.scope_stack.append(scope)
        self.report.node_scope[id(node)] = scope
        return scope
    def _pop(self):
        self.scope_stack.pop()
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            self.report.imported_names.add(bound)
            self._current().bound_names.add(bound)
        self.generic_visit(node)
    def visit_ImportFrom(self, node: ast.ImportFrom):
        for alias in node.names:
            bound = alias.asname or alias.name
            if bound != "*":
                self.report.imported_names.add(bound)
                self._current().bound_names.add(bound)
        self.generic_visit(node)
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._current().bound_names.add(node.name)
        self.report.node_scope[id(node)] = self._current()
        for d in node.decorator_list:
            self.visit(d)
        for d in node.args.defaults:
            self.visit(d)
        for d in node.args.kw_defaults:
            if d is not None:
                self.visit(d)
        if node.returns:
            self.visit(node.returns)
        for a in list(node.args.args) + list(node.args.posonlyargs) + list(node.args.kwonlyargs):
            if a.annotation:
                self.visit(a.annotation)
        scope = self._push("function", node.name, node)
        self._bind_arguments(node.args, scope)
        for stmt in node.body:
            self.visit(stmt)
        self._pop()
    visit_AsyncFunctionDef = visit_FunctionDef
    def _bind_arguments(self, args: ast.arguments, scope: ScopeInfo):
        all_args = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        for a in all_args:
            scope.bound_names.add(a.arg)
        if args.vararg:
            scope.bound_names.add(args.vararg.arg)
        if args.kwarg:
            scope.bound_names.add(args.kwarg.arg)
    def visit_ClassDef(self, node: ast.ClassDef):
        self._current().bound_names.add(node.name)
        self.report.node_scope[id(node)] = self._current()
        for base in node.bases:
            self.visit(base)
        for kw in node.keywords:
            self.visit(kw.value)
        for decorator in node.decorator_list:
            self.visit(decorator)
        scope = self._push("class", node.name, node)
        for stmt in node.body:
            self.visit(stmt)
        self._pop()
    def visit_Lambda(self, node: ast.Lambda):
        for d in node.args.defaults:
            self.visit(d)
        for d in node.args.kw_defaults:
            if d is not None:
                self.visit(d)
        scope = self._push("lambda", "<lambda>", node)
        self._bind_arguments(node.args, scope)
        self.visit(node.body)
        self._pop()
    def _visit_comprehension(self, node, kind: str):
        scope = self._push(kind, f"<{kind}>", node)
        for gen in node.generators:
            self.visit(gen.iter)
            self._bind_target(gen.target, scope)
            for cond in gen.ifs:
                self.visit(cond)
        if hasattr(node, "elt"):
            self.visit(node.elt)
        if hasattr(node, "key"):
            self.visit(node.key)
            self.visit(node.value)
        self._pop()
    def visit_ListComp(self, node):
        self._visit_comprehension(node, "comprehension")
    def visit_SetComp(self, node):
        self._visit_comprehension(node, "comprehension")
    def visit_DictComp(self, node):
        self._visit_comprehension(node, "comprehension")
    def visit_GeneratorExp(self, node):
        self._visit_comprehension(node, "comprehension")
    def _bind_target(self, target: ast.AST, scope: ScopeInfo):
        if isinstance(target, ast.Name):
            scope.bound_names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_target(elt, scope)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, scope)
    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            self._bind_target(target, self._current())
        self.generic_visit(node)
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._bind_target(node.target, self._current())
        self.generic_visit(node)
    def visit_AugAssign(self, node: ast.AugAssign):
        self._bind_target(node.target, self._current())
        self.generic_visit(node)
    def visit_NamedExpr(self, node: ast.NamedExpr):
        self._bind_target(node.target, self._current())
        self.generic_visit(node)
    def visit_For(self, node):
        self._bind_target(node.target, self._current())
        self.generic_visit(node)
    visit_AsyncFor = visit_For
    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars:
                self._bind_target(item.optional_vars, self._current())
        self.generic_visit(node)
    visit_AsyncWith = visit_With
    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.name:
            self._current().bound_names.add(node.name)
        self.generic_visit(node)
    def visit_Global(self, node: ast.Global):
        for name in node.names:
            self.report.global_unsafe_names.add(name)
            self.report.warnings.append(f"'global {name}' detected — name preserved for safety.")
        self.generic_visit(node)
    def visit_Nonlocal(self, node: ast.Nonlocal):
        for name in node.names:
            self.report.global_unsafe_names.add(name)
            self.report.warnings.append(f"'nonlocal {name}' detected — name preserved for safety.")
        self.generic_visit(node)
def analyze(tree: ast.AST, source_filename: str = "<module>") -> ScopeReport:
    module_scope = ScopeInfo(kind="module", name=source_filename, node=tree, parent=None)
    report = ScopeReport(module_scope=module_scope)
    report.node_scope[id(tree)] = module_scope
    dyn_visitor = _DynamicAccessVisitor()
    dyn_visitor.visit(tree)
    report.dynamic_referenced_names = dyn_visitor.dynamic_names
    if dyn_visitor.uses_eval_or_exec:
        report.warnings.append(
            "eval()/exec() detected — identifier mangling will be conservative "
            "in this module since dynamic code may reference names by string."
        )
    attr_visitor = _AttributeCollector()
    attr_visitor.visit(tree)
    report.attribute_names = attr_visitor.attribute_names
    kw_collector = _KeywordArgCollector()
    kw_collector.visit(tree)
    scope_builder = _ScopeBuilder(report)
    scope_builder.visit(tree)
    report.global_unsafe_names |= BUILTIN_NAMES
    report.global_unsafe_names |= report.dynamic_referenced_names
    report.global_unsafe_names |= report.imported_names
    report.global_unsafe_names |= report.attribute_names
    report.global_unsafe_names |= kw_collector.keyword_arg_names
    return report
CJK_POOL = [
    "玄", "龍", "炎", "影", "霧", "幻", "竜", "黒", "焔", "鬼",
    "妖", "冥", "蒼", "紅", "蝕", "爪", "翼", "刃", "雷", "焱",
    "魂", "闇", "光", "風", "水", "火", "土", "金", "木", "空",
    "夢", "咒", "符", "陣", "塔", "獄", "森", "岩", "氷", "焼",
    "黑", "青", "朱", "白", "天", "武", "雀", "虎", "霜", "麒",
    "麟", "凰", "鳳", "蛟", "灼", "斬", "咆", "哮", "牙", "骨",
]
HIRAGANA_POOL = [
    "あ", "い", "う", "え", "お", "か", "き", "く", "け", "こ",
    "さ", "し", "す", "せ", "そ", "た", "ち", "つ", "て", "と",
    "な", "に", "ぬ", "ね", "の", "は", "ひ", "ふ", "へ", "ほ",
    "ま", "み", "む", "め", "も", "や", "ゆ", "よ", "ら", "り",
    "る", "れ", "ろ", "わ", "を", "ん", "が", "ぎ", "ぐ", "げ",
    "ご", "ざ", "じ", "ず", "ぜ", "ぞ", "だ", "ぢ", "づ", "で",
    "ど", "ば", "び", "ぶ", "べ", "ぼ", "ぱ", "ぴ", "ぷ", "ぺ",
    "ぽ", "ぁ", "ぃ", "ぅ", "ぇ", "ぉ", "っ", "ゃ", "ゅ", "ょ",
    "ゔ", "ゖ", "ゕ", "ゐ", "ゑ",
]
_HIRAGANA_RARE = "𫸯"
THEME_SHORT_NAMES = [
    "Lufi", "Lufo", "Zoro", "Zyro", "Kai", "Kaidra", "Joyra", "Joya",
    "Haki", "Acey",
    "Naru", "Ruto", "Sasu", "Mada", "Dara", "Obi", "Kage", "Hoka",
    "Rin", "Shari", "Chakra", "Shino", "Nobi", "Chido", "Dori",
    "Rapto", "Rexo", "Spino", "Saura", "Dilo", "Brachio",
    "Pika", "Chu", "Zard", "Chari", "Mew", "Quaza", "Rayq", "Dia",
    "Palk", "Kia", "Gira", "Tina", "Arce", "Grou", "Kyo", "Reshi",
    "Shira", "Zek", "Krom", "Lugia", "Grenin",
]
THEME_FULL_NAMES = [
    "Luffy", "Zoro", "Kaido", "HaKiBaVuong", "JoyBoy", "Shanks",
    "Akainu",
    "Naruto", "Sasuke", "Itachi", "Madara", "Obito", "Hokage",
    "Sharingan", "Chakra", "Shinobi", "Chidori", "Minato", "Kakashi",
    "Raptor", "T_Rex", "Spinosaurus", "Dilophosaurus", "Velociraptor",
    "Pikachu", "Charizard", "Mewtwo", "Rayquaza", "Dialga", "Palkia",
    "Giratina", "Arceus", "Groudon", "Kyogre", "Reshiram", "Zekrom",
    "Kyurem", "Garchomp",
]
def _is_valid_python_identifier(name: str) -> bool:
    if not name.isidentifier():
        return False
    if unicodedata.normalize("NFKC", name) != name:
        return False
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        return False
    return True
def resolve_identifier_style(style: str, build_seed: int) -> str:
    if style != "auto":
        return style if style in ("cjk", "ascii", "hiragana") else "cjk"
    return ("cjk", "hiragana", "ascii")[
        derive_seed(build_seed, "naming_style") % 3]
class RuneNaming:
    def __init__(self, style: str = "cjk", ascii_prefix: str = "_zy",
                 build_seed: Optional[int] = None,
                 avoid: Optional[Set[str]] = None):
        self.build_seed = build_seed if build_seed is not None else 0
        self.style = resolve_identifier_style(style, self.build_seed)
        self.ascii_prefix = ascii_prefix
        self._used_names: Set[str] = set()
        self._avoid: Set[str] = set(avoid) if avoid else set()
        self._theme_rng = _seeded_rng(self.build_seed, "naming_theme")
        self._theme_prob = 0.55
    def generate(self, seed_key: str) -> str:
        combined_key = f"{self.build_seed}::{seed_key}"
        digest = hashlib.sha256(combined_key.encode()).hexdigest()
        if self._theme_rng.random() < self._theme_prob:
            use_short = self._theme_rng.random() < 0.78
            pool = THEME_SHORT_NAMES if use_short else THEME_FULL_NAMES
            base = pool[int(digest[2:6], 16) % len(pool)]
            candidate = (base if self._theme_rng.random() < 0.5
                         else f"{base}{digest[8:10]}")
        elif self.style == "cjk":
            candidate = self._generate_cjk(digest)
        elif self.style == "hiragana":
            candidate = self._generate_hiragana(digest)
        else:
            candidate = self._generate_ascii(digest)
        candidate = self._ensure_unique_and_valid(candidate, digest)
        self._used_names.add(candidate)
        return candidate
    def _generate_ascii(self, digest: str) -> str:
        return f"{self.ascii_prefix}_{digest[:6]}"
    def _generate_cjk(self, digest: str) -> str:
        idx1 = int(digest[0:4], 16) % len(CJK_POOL)
        idx2 = int(digest[4:8], 16) % len(CJK_POOL)
        glyphs = CJK_POOL[idx1] + CJK_POOL[idx2]
        suffix = digest[8:14]
        return f"{glyphs}_{suffix}"
    def _generate_hiragana(self, digest: str) -> str:
        pool = HIRAGANA_POOL
        n = len(pool)
        rare = _HIRAGANA_RARE
        length = 15 + (int(digest[0:2], 16) % 11)
        result: List[str] = []
        d = digest
        for i in range(length):
            off = (i * 3) % 60
            val = int(d[off:off + 2], 16) + i * 7
            idx = val % n
            rp_off = (off + 4) % 60
            rare_probe = int(d[rp_off:rp_off + 2], 16)
            if result and rare_probe < 30:
                result.append(rare)
            else:
                result.append(pool[idx])
        return "".join(result)
    def _ensure_unique_and_valid(self, candidate: str, digest: str) -> str:
        attempt = candidate
        salt = 0
        while ((not _is_valid_python_identifier(attempt))
               or (attempt in self._used_names)
               or (attempt in self._avoid)):
            salt += 1
            if not _is_valid_python_identifier(candidate):
                attempt = f"{self.ascii_prefix}_{digest[:6]}_{salt}"
            else:
                extra = hashlib.sha256(f"{digest}{salt}".encode()).hexdigest()[:4]
                attempt = f"{candidate}{extra}"
            if not _is_valid_python_identifier(attempt):
                attempt = f"{self.ascii_prefix}_{hashlib.sha256(attempt.encode()).hexdigest()[:8]}"
        return attempt
@dataclass
class RuneRegistry:
    mapping: Dict[str, str] = field(default_factory=dict)
    counter: int = 0
    def record(self, key: str, generated: str) -> str:
        self.mapping[key] = generated
        self.counter += 1
        return generated
    def as_dict(self) -> Dict[str, str]:
        return dict(self.mapping)
def _scope_path(scope: ScopeInfo) -> str:
    parts = []
    s: Optional[ScopeInfo] = scope
    while s is not None:
        parts.append(f"{s.kind}:{s.name}")
        s = s.parent
    return "/".join(reversed(parts))
class _RenamePlanner:
    def __init__(self, report: ScopeReport, config: DragonConfig,
                 registry: RuneRegistry, naming: RuneNaming):
        self.report = report
        self.config = config
        self.registry = registry
        self.naming = naming
        self.plan: Dict[int, Dict[str, str]] = {}
        self.module_plan: Dict[str, str] = {}
    def build(self):
        self._visit_scope(self.report.module_scope, is_module=True)
    def _visit_scope(self, scope: ScopeInfo, is_module: bool):
        if is_module:
            if self.config.rename_module_level:
                for name in sorted(scope.bound_names):
                    if self._eligible(name, scope):
                        path = _scope_path(scope)
                        key = f"{path}::{name}"
                        generated = self.naming.generate(key)
                        self.registry.record(key, generated)
                        self.module_plan[name] = generated
            self.plan[id(scope.node)] = self.module_plan
        else:
            local_plan: Dict[str, str] = {}
            for name in sorted(scope.bound_names):
                if self._eligible(name, scope):
                    path = _scope_path(scope)
                    key = f"{path}::{name}"
                    generated = self.naming.generate(key)
                    self.registry.record(key, generated)
                    local_plan[name] = generated
            self.plan[id(scope.node)] = local_plan
        for child in scope.children:
            self._visit_scope(child, is_module=False)
    def _eligible(self, name: str, scope: ScopeInfo) -> bool:
        if name in self.report.global_unsafe_names:
            return False
        if is_dunder(name):
            return False
        if name in ("self", "cls"):
            return False
        return True
class _Renamer(ast.NodeTransformer):
    def __init__(self, report: ScopeReport, planner: _RenamePlanner, config: DragonConfig):
        self.report = report
        self.planner = planner
        self.config = config
        self.scope_chain = [report.module_scope]
    def visit(self, node):
        if is_generated(node):
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)):
                _new = self._lookup(node.value.func.id)
                if _new:
                    node.value.func.id = _new
            return node
        return super().visit(node)
    def _lookup(self, name: str):
        for scope in reversed(self.scope_chain):
            local_plan = self.planner.plan.get(id(scope.node))
            if local_plan and name in local_plan:
                return local_plan[name]
        return None
    def visit_FunctionDef(self, node: ast.FunctionDef):
        scope = self.report.node_scope.get(id(node))
        new_func_name = self._lookup(node.name)
        if new_func_name and self.config.rename_functions:
            node.name = new_func_name
        if scope and scope.kind == "function":
            node.decorator_list = [self.visit(d) for d in node.decorator_list]
            node.args.defaults = [self.visit(d) for d in node.args.defaults]
            node.args.kw_defaults = [
                self.visit(d) if d is not None else None
                for d in node.args.kw_defaults
            ]
            if node.returns:
                node.returns = self.visit(node.returns)
            for a in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                if a.annotation is not None:
                    a.annotation = self.visit(a.annotation)
            if node.args.vararg and node.args.vararg.annotation is not None:
                node.args.vararg.annotation = self.visit(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                node.args.kwarg.annotation = self.visit(node.args.kwarg.annotation)
            self.scope_chain.append(scope)
            local_plan = self.planner.plan.get(id(scope.node), {})
            if self.config.rename_arguments:
                self._rename_args(node.args, local_plan)
            node.body = [self.visit(stmt) for stmt in node.body]
            self.scope_chain.pop()
        else:
            self.generic_visit(node)
        return node
    visit_AsyncFunctionDef = visit_FunctionDef
    def _rename_args(self, args: ast.arguments, local_plan: Dict[str, str]):
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            for a in group:
                if a.arg in local_plan:
                    a.arg = local_plan[a.arg]
        if args.vararg and args.vararg.arg in local_plan:
            args.vararg.arg = local_plan[args.vararg.arg]
        if args.kwarg and args.kwarg.arg in local_plan:
            args.kwarg.arg = local_plan[args.kwarg.arg]
    def visit_ClassDef(self, node: ast.ClassDef):
        new_class_name = self._lookup(node.name)
        if new_class_name and self.config.rename_classes:
            node.name = new_class_name
        scope = self.report.node_scope.get(id(node))
        if scope and scope.kind == "class":
            node.bases = [self.visit(b) for b in node.bases]
            node.keywords = [
                ast.keyword(arg=kw.arg, value=self.visit(kw.value))
                for kw in node.keywords
            ]
            node.decorator_list = [self.visit(d) for d in node.decorator_list]
            self.scope_chain.append(scope)
            node.body = [self.visit(stmt) for stmt in node.body]
            self.scope_chain.pop()
        else:
            self.generic_visit(node)
        return node
    def visit_Lambda(self, node: ast.Lambda):
        scope = self.report.node_scope.get(id(node))
        if scope and scope.kind == "lambda":
            node.args.defaults = [self.visit(d) for d in node.args.defaults]
            node.args.kw_defaults = [
                self.visit(d) if d is not None else None
                for d in node.args.kw_defaults
            ]
            self.scope_chain.append(scope)
            local_plan = self.planner.plan.get(id(scope.node), {})
            self._rename_args(node.args, local_plan)
            node.body = self.visit(node.body)
            self.scope_chain.pop()
        else:
            self.generic_visit(node)
        return node
    def _visit_comprehension_node(self, node):
        scope = self.report.node_scope.get(id(node))
        if scope and scope.kind == "comprehension":
            self.scope_chain.append(scope)
            self.generic_visit(node)
            self.scope_chain.pop()
        else:
            self.generic_visit(node)
        return node
    def visit_ListComp(self, node):
        return self._visit_comprehension_node(node)
    def visit_SetComp(self, node):
        return self._visit_comprehension_node(node)
    def visit_DictComp(self, node):
        return self._visit_comprehension_node(node)
    def visit_GeneratorExp(self, node):
        return self._visit_comprehension_node(node)
    def visit_Name(self, node: ast.Name):
        new_name = self._lookup(node.id)
        if new_name:
            node.id = new_name
        return node
    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.name:
            new_name = self._lookup(node.name)
            if new_name:
                node.name = new_name
        self.generic_visit(node)
        return node
def mangle_with_naming(tree: ast.AST, report: ScopeReport, config: DragonConfig,
                        naming: "RuneNaming") -> RuneRegistry:
    registry = RuneRegistry()
    planner = _RenamePlanner(report, config, registry, naming)
    planner.build()
    renamer = _Renamer(report, planner, config)
    renamer.visit(tree)
    ast.fix_missing_locations(tree)
    return registry
_GENERATED_ATTR = "_dzydkh_generated"
def mark_generated(node: ast.AST) -> ast.AST:
    for n in ast.walk(node):
        setattr(n, _GENERATED_ATTR, True)
    return node
def is_generated(node: ast.AST) -> bool:
    return getattr(node, _GENERATED_ATTR, False)
@dataclass
class PassCapability:
    name: str
    requires: Tuple[str, ...] = ()
    conflicts: Tuple[str, ...] = ()
    preserves: Tuple[str, ...] = ()
    estimated_size_delta: float = 1.05
    estimated_runtime_cost: str = "low"
    semantic_risk: str = "low"
    seed_namespace: str = "expansion"
PASS_CAPABILITIES: Dict[str, PassCapability] = {
    "arithmetic_decomposition": PassCapability(
        "arithmetic_decomposition", preserves=("exact_integer_math",),
        estimated_size_delta=1.15, seed_namespace="expansion"),
    "harmless_wrapper": PassCapability(
        "harmless_wrapper",
        preserves=("call_semantics",), estimated_size_delta=1.08,
        seed_namespace="expansion"),
    "decoy_statement": PassCapability(
        "decoy_statement", requires=("functions_present_or_wrappable",),
        estimated_size_delta=1.02, seed_namespace="expansion"),
    "opaque_predicate": PassCapability(
        "opaque_predicate", preserves=("value_identity_of_real_arm",),
        estimated_size_delta=1.10, seed_namespace="expansion"),
    "boolean_algebra": PassCapability(
        "boolean_algebra", preserves=("truth_tables",),
        estimated_size_delta=1.12, semantic_risk="low",
        seed_namespace="expansion"),
    "memory_error_dispatcher": PassCapability(
        "memory_error_dispatcher",
        conflicts=("async_generators_present",),
        preserves=("statement_order_and_effect",),
        estimated_size_delta=1.25, estimated_runtime_cost="medium",
        semantic_risk="medium", seed_namespace="expansion"),
    "builtins_indirection": PassCapability(
        "builtins_indirection",
        requires=("scope_analysis",),
        preserves=("builtin_resolution",),
        estimated_size_delta=1.06, semantic_risk="medium",
        seed_namespace="helpers"),
    "return_split": PassCapability(
        "return_split", requires=("functions_present_or_wrappable",),
        preserves=("evaluation_order",), estimated_size_delta=1.04,
        seed_namespace="expansion"),
    "dead_path": PassCapability(
        "dead_path", requires=("functions_present_or_wrappable",),
        preserves=("never_executes_guards",), estimated_size_delta=1.03,
        seed_namespace="expansion"),
    "chain_link": PassCapability(
        "chain_link", requires=("functions_present_or_wrappable",),
        preserves=("statement_order_and_build_time_verified_tautologies",),
        estimated_size_delta=1.12, estimated_runtime_cost="low",
        semantic_risk="low", seed_namespace="expansion"),
    "handler_embed": PassCapability(
        "handler_embed", requires=("functions_present_or_wrappable",),
        preserves=("statement_order_and_effect_unique_exception_token",),
        estimated_size_delta=1.15, estimated_runtime_cost="low",
        semantic_risk="low", seed_namespace="expansion"),
    "int_hide": PassCapability(
        "int_hide", preserves=("exact_integer_values_via_subtract_big",),
        estimated_size_delta=1.06, semantic_risk="low",
        seed_namespace="literals"),
    "dead_bloat": PassCapability(
        "dead_bloat", requires=("functions_present_or_wrappable",),
        preserves=("dead_positions_only_never_executes",),
        estimated_size_delta=1.10, semantic_risk="low",
        seed_namespace="expansion"),
    "decompiler_traps": PassCapability(
        "decompiler_traps", requires=("functions_present_or_wrappable",),
        preserves=("statement_order_verified_token_match_verified_tests",),
        estimated_size_delta=1.18, estimated_runtime_cost="low",
        semantic_risk="low", seed_namespace="expansion"),    "globals_storage": PassCapability(
        "globals_storage", requires=("scope_analysis",),
        preserves=("module_global_identity",),
        estimated_size_delta=1.20, estimated_runtime_cost="medium",
        semantic_risk="high", seed_namespace="helpers"),
    "while_diversify": PassCapability(
        "while_diversify", preserves=("loop_condition_recheck_each_iter",),
        estimated_size_delta=1.05, semantic_risk="low",
        seed_namespace="cfg"),
    "branch_normalize": PassCapability(
        "branch_normalize", preserves=("test_first_evaluation",),
        estimated_size_delta=0.98, semantic_risk="low",
        seed_namespace="cfg"),
    "int_pool_indirection": PassCapability(
        "int_pool_indirection",
        preserves=("exact_integer_values_via_xor_involution",),
        estimated_size_delta=1.04, semantic_risk="low",
        seed_namespace="literals"),
    "lambda_thunk": PassCapability(
        "lambda_thunk",
        requires=("functions_present_or_wrappable",),
        preserves=("evaluation_order_and_call_semantics",),
        estimated_size_delta=1.06, semantic_risk="low",
        seed_namespace="expansion"),
    "tautology_guard": PassCapability(
        "tautology_guard",
        preserves=("condition_truthiness_and_single_evaluation",),
        estimated_size_delta=1.05, semantic_risk="low",
        seed_namespace="cfg"),
    "condition_swap": PassCapability(
        "condition_swap",
        preserves=("both_arms_executed_exclusively",),
        estimated_size_delta=1.00, semantic_risk="low",
        seed_namespace="cfg"),
    "entry_guard": PassCapability(
        "entry_guard",
        preserves=("tautology_true_entry_predicate",),
        estimated_size_delta=1.03, semantic_risk="low",
        seed_namespace="cfg"),
    "if_conjunct_split": PassCapability(
        "if_conjunct_split",
        preserves=("single_body_execution_via_nesting_or_elif",),
        estimated_size_delta=1.08, semantic_risk="low",
        seed_namespace="cfg"),
    "zero_div_wrap": PassCapability(
        "zero_div_wrap",
        conflicts=("async_generators_present",),
        preserves=("statement_order_and_effect",),
        estimated_size_delta=1.18, estimated_runtime_cost="low",
        semantic_risk="medium", seed_namespace="cfg"),
    "fstring_split": PassCapability(
        "fstring_split",
        preserves=("formatted_value_evaluation_order_and_conversions",),
        estimated_size_delta=1.12, semantic_risk="low",
        seed_namespace="literals"),
    "for_to_while": PassCapability(
        "for_to_while",
        preserves=("iteration_semantics_and_body_order",),
        estimated_size_delta=1.15, estimated_runtime_cost="low",
        semantic_risk="medium", seed_namespace="cfg"),
}
_STAGE_ORDER: Tuple[str, ...] = (
    "ANALYZE", "NORMALIZE", "RENAME", "LITERAL_DATA", "IR_VM_SELECTION",
    "CFG_TRANSFORMS", "STRUCTURAL_TRANSFORMS", "RUNTIME_PACKAGING",
    "INTEGRITY", "VALIDATE", "SIZE_GOVERNOR", "EMIT",
)
class PipelinePlan:
    def __init__(self, context: Dict[str, bool]):
        self.context = dict(context)
    def resolve(self, requested: List[Tuple[str, Any]]) -> Tuple[List[Any], List[str]]:
        notes: List[str] = []
        accepted: List[Tuple[str, Any]] = []
        def _satisfied(req: str) -> bool:
            if req in self.context:
                return bool(self.context[req])
            return True
        for cap_name, instance in requested:
            cap = PASS_CAPABILITIES.get(cap_name)
            if cap is None:
                accepted.append((cap_name, instance))
                continue
            unmet = [r for r in cap.requires if not _satisfied(r)]
            if unmet:
                notes.append(f"[PLAN] drop '{cap_name}': unmet requires {unmet}")
                continue
            clash = None
            for done_name, _inst in accepted:
                done_cap = PASS_CAPABILITIES.get(done_name)
                if done_cap and cap_name in done_cap.conflicts:
                    clash = done_name
                    break
                if done_name in cap.conflicts:
                    clash = done_name
                    break
            if clash is not None:
                notes.append(f"[PLAN] drop '{cap_name}': conflicts with '{clash}'")
                continue
            accepted.append((cap_name, instance))
        return [inst for _, inst in accepted], notes
    @staticmethod
    def stage_view(stages_run: List[str]) -> Dict[str, bool]:
        joined = "|".join(stages_run)
        return {
            "ANALYZE": "ANALYZE" in joined or "RUNE_MANGLER" in joined,
            "RENAME": "RUNE_MANGLER" in joined,
            "LITERAL_DATA": any(k in joined for k in (
                "STRING_DEDUP", "MIXED_STRING_ROUTER", "EMBER_CIPHER",
                "CONSTANT_PROTECTOR", "REQUESTS_PROTECT")),
            "IR_VM_SELECTION": "VM_VIRTUALIZATION" in joined,
            "CFG_TRANSFORMS": any(k in joined for k in (
                "WYRM_FLOW", "CFG_ENGINE")),
            "STRUCTURAL_TRANSFORMS": "AST_STRUCTURAL_EXPANSION" in joined
                                     or "MODULE_ENTRY_WRAP" in joined,
            "RUNTIME_PACKAGING": "USER_PAYLOAD_ARCH" in joined,
            "INTEGRITY": "INTEGRITY_METADATA" in joined,
            "VALIDATE": "VALIDATE" in joined,
            "SIZE_GOVERNOR": any(k in joined for k in (
                "BOOST", "SIZE_GOVERNOR")),
            "EMIT": "HEADER_INJECT" in joined,
        }
class _SkipGeneratedTransformer(ast.NodeTransformer):
    def visit(self, node):
        if is_generated(node):
            return node
        return super().visit(node)
class TransformationPass(ABC):
    name: str = "unnamed"
    @abstractmethod
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        ...
class ArithmeticDecompositionPass(TransformationPass):
    name = "arithmetic_decomposition"
    SAFE_OPS = (ast.Add, ast.Sub, ast.Mult)
    def __init__(self, coverage: float = 0.5):
        self.coverage = min(0.95, max(0.05, coverage))
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = 0
        budget = [200]
        coverage = self.coverage
        class _Visitor(_SkipGeneratedTransformer):
            def visit_BinOp(inner_self, node: ast.BinOp):
                inner_self.generic_visit(node)
                nonlocal count
                if budget[0] <= 0:
                    return node
                if not isinstance(node.op, ArithmeticDecompositionPass.SAFE_OPS):
                    return node
                if not (isinstance(node.left, (ast.Name, ast.Constant)) and
                        isinstance(node.right, (ast.Name, ast.Constant))):
                    return node
                if isinstance(node.left, ast.Constant) and not isinstance(node.left.value, (int, float)):
                    return node
                if isinstance(node.right, ast.Constant) and not isinstance(node.right.value, (int, float)):
                    return node
                if rng.random() > coverage:
                    return node
                if isinstance(node.op, ast.Mult):
                    new_left = ast.BinOp(left=node.left, op=ast.Mult(), right=ast.Constant(value=1))
                    new_right = ast.BinOp(left=node.right, op=ast.Mult(), right=ast.Constant(value=1))
                else:
                    new_left = ast.BinOp(left=node.left, op=ast.Add(), right=ast.Constant(value=0))
                    new_right = ast.BinOp(left=node.right, op=ast.Sub(), right=ast.Constant(value=0))
                node.left = ast.copy_location(new_left, node.left)
                node.right = ast.copy_location(new_right, node.right)
                count += 1
                budget[0] -= 1
                return node
        _Visitor().visit(tree)
        return count
class HarmlessWrapperPass(TransformationPass):
    name = "harmless_wrapper"
    HELPER_NAME = "_dzydkh_id"
    def __init__(self, coverage: float = 0.3, build_seed: Optional[int] = None):
        self.coverage = min(0.9, max(0.05, coverage))
        if build_seed is not None:
            self.helper_name = (
                f"_zi{hashlib.sha256(f'harmless::{build_seed}'.encode()).hexdigest()[:10]}"
            )
        else:
            self.helper_name = self.HELPER_NAME
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = 0
        budget = [80]
        skip_prob = max(0.05, 1.0 - self.coverage)
        helper_name = self.helper_name
        class _Visitor(_SkipGeneratedTransformer):
            def __init__(inner_self):
                inner_self.in_condition = False
            def visit_If(inner_self, node: ast.If):
                inner_self.in_condition = True
                node.test = inner_self.visit(node.test)
                inner_self.in_condition = False
                node.body = [inner_self.visit(s) for s in node.body]
                node.orelse = [inner_self.visit(s) for s in node.orelse]
                return node
            def visit_While(inner_self, node: ast.While):
                inner_self.in_condition = True
                node.test = inner_self.visit(node.test)
                inner_self.in_condition = False
                node.body = [inner_self.visit(s) for s in node.body]
                node.orelse = [inner_self.visit(s) for s in node.orelse]
                return node
            def visit_BinOp(inner_self, node: ast.BinOp):
                inner_self.generic_visit(node)
                nonlocal count
                if inner_self.in_condition or budget[0] <= 0:
                    return node
                if rng.random() < skip_prob:
                    return node
                wrapped = ast.Call(
                    func=ast.Name(id=helper_name, ctx=ast.Load()),
                    args=[node], keywords=[],
                )
                count += 1
                budget[0] -= 1
                return ast.copy_location(wrapped, node)
        _Visitor().visit(tree)
        if count > 0:
            _exists = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and n.name == helper_name for n in tree.body)
            if not _exists:
                helper_ast = ast.parse(
                    f"def {helper_name}(v):\n    return v\n"
                ).body
                insert_at = _module_insert_index(tree)
                tree.body[insert_at:insert_at] = helper_ast
        return count
class DecoyStatementPass(TransformationPass):
    name = "decoy_statement"
    LEVEL_COUNTS = {"minimal": 0, "light": 1, "high": 2, "extreme": 4}
    def __init__(self, naming: "RuneNaming", expansion_level: str):
        self.naming = naming
        self.expansion_level = expansion_level
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        n_per_func = self.LEVEL_COUNTS.get(self.expansion_level, 1)
        if n_per_func <= 0:
            return 0
        count = 0
        naming = self.naming
        func_visit_counter = [0]
        class _Visitor(_SkipGeneratedTransformer):
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                inner_self.generic_visit(node)
                nonlocal count
                if not node.body:
                    return node
                func_key = f"{node.name}#{func_visit_counter[0]}"
                func_visit_counter[0] += 1
                decoys = []
                for _ in range(n_per_func):
                    decoy_name = naming.generate(f"decoy::{func_key}::{len(decoys)}")
                    decoy_value = rng.randint(-1000, 1000)
                    decoy_stmt = ast.Assign(
                        targets=[ast.Name(id=decoy_name, ctx=ast.Store())],
                        value=ast.Constant(value=decoy_value),
                    )
                    mark_generated(decoy_stmt)
                    decoys.append(decoy_stmt)
                    count += 1
                insert_at = 0
                if node.body and isinstance(node.body[0], ast.Expr) and \
                        isinstance(node.body[0].value, ast.Constant) and \
                        isinstance(node.body[0].value.value, str):
                    insert_at = 1
                node.body[insert_at:insert_at] = decoys
                return node
            visit_AsyncFunctionDef = visit_FunctionDef
        _Visitor().visit(tree)
        return count
class GlobalsStoragePass(TransformationPass):
    name = "globals_storage"
    def __init__(self, naming: "RuneNaming", build_seed: int,
                 unsafe_names: Set[str]):
        self.naming = naming
        self.build_seed = build_seed
        self.unsafe_names = unsafe_names
        self._key_cache: Dict[str, List[Tuple[int, int]]] = {}
        self._occ: List[int] = [0]
    def _get_pairs(self, name: str) -> List[Tuple[int, int]]:
        if name not in self._key_cache:
            data = name.encode("utf-8")
            pairs: List[Tuple[int, int]] = []
            for i, b in enumerate(data):
                kd = hashlib.sha256(
                    f"{self.build_seed}::gstore::{name}::{i}".encode()
                ).digest()
                key = kd[0]
                pairs.append((key, b ^ key))
            self._key_cache[name] = pairs
        return self._key_cache[name]
    def _build_key_expr(self, name: str) -> ast.expr:
        pairs = self._get_pairs(name)
        lambdas: List[ast.expr] = []
        occ = self._occ[0]
        for j, (key, enc) in enumerate(pairs):
            param = self.naming.generate(f"gsp::{name}::{j}::{occ}::{self.build_seed}")
            lam = ast.Lambda(
                args=ast.arguments(
                    posonlyargs=[], args=[ast.arg(arg=param)],
                    kwonlyargs=[], kw_defaults=[], defaults=[],
                    vararg=None, kwarg=None,
                ),
                body=ast.BinOp(
                    left=ast.Name(id=param, ctx=ast.Load()),
                    op=ast.BitXor(),
                    right=ast.Constant(value=key),
                ),
            )
            call = ast.Call(func=lam, args=[ast.Constant(value=enc)], keywords=[])
            lambdas.append(call)
        self._occ[0] += 1
        bytes_call = ast.Call(
            func=ast.Name(id="bytes", ctx=ast.Load()),
            args=[ast.List(elts=lambdas, ctx=ast.Load())],
            keywords=[],
        )
        decode_call = ast.Call(
            func=ast.Attribute(value=bytes_call, attr="decode", ctx=ast.Load()),
            args=[ast.Constant(value="utf-8")],
            keywords=[],
        )
        return decode_call
    def _globals_store(self, name: str, value: ast.expr,
                       lineno: int, col_offset: int) -> ast.Assign:
        subscript = ast.Subscript(
            value=ast.Call(
                func=ast.Name(id="globals", ctx=ast.Load()),
                args=[], keywords=[],
            ),
            slice=self._build_key_expr(name),
            ctx=ast.Store(),
        )
        node = ast.Assign(targets=[subscript], value=value, lineno=lineno,
                          col_offset=col_offset)
        ast.fix_missing_locations(node)
        return node
    def _globals_load(self, name: str, lineno: int, col_offset: int) -> ast.expr:
        node = ast.Subscript(
            value=ast.Call(
                func=ast.Name(id="globals", ctx=ast.Load()),
                args=[], keywords=[],
            ),
            slice=self._build_key_expr(name),
            ctx=ast.Load(),
            lineno=lineno,
            col_offset=col_offset,
        )
        ast.fix_missing_locations(node)
        return node
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        eligible: Set[str] = set()
        funcdef_names: Set[str] = set()
        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                funcdef_names.add(stmt.name)
            if (isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and not is_generated(stmt)):
                n = stmt.targets[0].id
                if (not is_dunder(n)
                        and n not in BUILTIN_NAMES
                        and n not in self.unsafe_names
                        and n not in funcdef_names):
                    eligible.add(n)
        if not eligible:
            return 0
        count = 0
        class _ModuleRewriter(ast.NodeTransformer):
            def __init__(inner_self):
                inner_self._depth = 0
            def visit_FunctionDef(inner_self, node):
                inner_self._depth += 1
                inner_self.generic_visit(node)
                inner_self._depth -= 1
                return node
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_ClassDef(inner_self, node):
                inner_self._depth += 1
                inner_self.generic_visit(node)
                inner_self._depth -= 1
                return node
            def visit_Assign(inner_self, node: ast.Assign):
                inner_self.generic_visit(node)
                if inner_self._depth > 0:
                    return node
                if (len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id in eligible):
                    nonlocal count
                    count += 1
                    return self._globals_store(
                        node.targets[0].id, node.value,
                        node.lineno, node.col_offset,
                    )
                return node
            def visit_Name(inner_self, node: ast.Name):
                if (inner_self._depth == 0
                        and isinstance(node.ctx, ast.Load)
                        and node.id in eligible):
                    return self._globals_load(node.id, node.lineno, node.col_offset)
                return node
        _ModuleRewriter().visit(tree)
        return count
class MemoryErrorDispatcherPass(TransformationPass):
    name = "memory_error_dispatcher"
    _SKIP_TYPES = (
        ast.Global, ast.Nonlocal, ast.Return, ast.Yield,
        ast.YieldFrom, ast.Raise, ast.Break, ast.Continue,
        ast.Pass, ast.Import, ast.ImportFrom,
    )
    def __init__(self, naming: "RuneNaming", expansion_level: str,
                 cap_override: Optional[int] = None,
                 junk_override: Optional[int] = None,
                 global_cap: Optional[int] = None):
        self.naming = naming
        self.expansion_level = expansion_level
        base_caps = {"minimal": 0, "light": 2, "high": 4, "extreme": 6}
        self._cap = cap_override if cap_override is not None else base_caps.get(expansion_level, 2)
        base_junk = {"minimal": 1, "light": 1, "high": 2, "extreme": 3}
        self._n_junk = junk_override if junk_override is not None else base_junk.get(expansion_level, 1)
        self._counter = [0]
        self.global_cap = global_cap
        self._global_wrapped = [0]
    def _is_docstring(self, stmt: ast.stmt) -> bool:
        return (isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str))
    def _is_unsafe_body(self, body: List[ast.stmt]) -> bool:
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await,
                                 ast.Global, ast.Nonlocal, ast.Match)):
                return True
        return False
    def _already_has_memory_error(self, body: List[ast.stmt]) -> bool:
        for stmt in body:
            if (isinstance(stmt, ast.Try)
                    and any(isinstance(h.type, ast.Name) and h.type.id == "MemoryError"
                            for h in stmt.handlers)):
                return True
        return False
    def _bare_junk_assign(self, rng: "random.Random") -> ast.Assign:
        junk_name = self.naming.generate(
            f"med::bjunk::{self._counter[0]}"
        )
        self._counter[0] += 1
        st = ast.Assign(
            targets=[ast.Name(id=junk_name, ctx=ast.Store())],
            value=ast.Constant(value=rng.randint(1_048_575, 281_474_976_710_655)),
            lineno=0, col_offset=0,
        )
        mark_generated(st)
        ast.fix_missing_locations(st)
        return st
    def _make_junk_case(self, err_name: str, case_value: int,
                        rng: "random.Random") -> ast.If:
        junk_name = self.naming.generate(
            f"med::junk::{case_value}::{self._counter[0]}"
        )
        self._counter[0] += 1
        return ast.If(
            test=ast.Compare(
                left=ast.Subscript(
                    value=ast.Attribute(
                        value=ast.Name(id=err_name, ctx=ast.Load()),
                        attr="args", ctx=ast.Load(),
                    ),
                    slice=ast.Constant(value=0),
                    ctx=ast.Load(),
                ),
                ops=[ast.Eq()],
                comparators=[ast.Constant(value=case_value)],
            ),
            body=[ast.Assign(
                targets=[ast.Name(id=junk_name, ctx=ast.Store())],
                value=ast.Constant(value=rng.randint(1_048_575, 281_474_976_710_655)),
                lineno=0, col_offset=0,
            )],
            orelse=[],
        )
    def _wrap_statement(self, stmt: ast.stmt, rng: "random.Random") -> List[ast.stmt]:
        counter_name = self.naming.generate(f"med::counter::{self._counter[0]}")
        err_name = self.naming.generate(f"med::err::{self._counter[0]}")
        self._counter[0] += 1
        real_case = ast.If(
            test=ast.Compare(
                left=ast.Subscript(
                    value=ast.Attribute(
                        value=ast.Name(id=err_name, ctx=ast.Load()),
                        attr="args", ctx=ast.Load(),
                    ),
                    slice=ast.Constant(value=0),
                    ctx=ast.Load(),
                ),
                ops=[ast.Eq()],
                comparators=[ast.Constant(value=1)],
            ),
            body=[stmt],
            orelse=[],
        )
        junk_cases = [
            self._make_junk_case(err_name, 2 + j, rng)
            for j in range(self._n_junk)
        ]
        except_body = [real_case] + junk_cases
        init_stmt = ast.Assign(
            targets=[ast.Name(id=counter_name, ctx=ast.Store())],
            value=ast.Constant(value=0),
            lineno=0, col_offset=0,
        )
        inc_stmt = ast.AugAssign(
            target=ast.Name(id=counter_name, ctx=ast.Store()),
            op=ast.Add(),
            value=ast.Constant(value=1),
            lineno=0, col_offset=0,
        )
        try_stmt = ast.Try(
            body=[ast.Raise(
                exc=ast.Call(
                    func=ast.Name(id="MemoryError", ctx=ast.Load()),
                    args=[ast.Name(id=counter_name, ctx=ast.Load())],
                    keywords=[],
                ),
                cause=None,
            )],
            handlers=[ast.ExceptHandler(
                type=ast.Name(id="MemoryError", ctx=ast.Load()),
                name=err_name,
                body=except_body,
            )],
            orelse=[], finalbody=[],
        )
        if rng.random() < 0.5:
            _m_a = self.naming.generate(f"med::msuba::{self._counter[0]}")
            _m_b = self.naming.generate(f"med::msubb::{self._counter[0]}")
            _raise_true = ast.Raise(
                exc=ast.Call(func=ast.Name(id="MemoryError", ctx=ast.Load()),
                             args=[ast.Name(id=counter_name, ctx=ast.Load())],
                             keywords=[]),
                cause=None,
            )
            _match = ast.Match(
                subject=ast.Compare(
                    left=ast.Constant(value=_m_a), ops=[ast.Eq()],
                    comparators=[ast.Constant(value=_m_b)]),
                cases=[
                    ast.match_case(
                        pattern=ast.MatchSingleton(value=True),
                        body=[_raise_true]),
                    ast.match_case(
                        pattern=ast.MatchSingleton(value=False),
                        body=[self._bare_junk_assign(rng)]),
                ],
            )
            mark_generated(_match)
            try_stmt.body.insert(0, _match)
        ast.fix_missing_locations(init_stmt)
        ast.fix_missing_locations(inc_stmt)
        ast.fix_missing_locations(try_stmt)
        mark_generated(init_stmt)
        mark_generated(inc_stmt)
        for raise_stmt in try_stmt.body:
            mark_generated(raise_stmt)
        mark_generated(real_case.test)
        for jc in junk_cases:
            mark_generated(jc)
        return [init_stmt, inc_stmt, try_stmt]
    def _process_body(self, body: List[ast.stmt],
                      rng: "random.Random") -> List[ast.stmt]:
        if self._cap == 0:
            return body
        if self.global_cap is not None and self._global_wrapped[0] >= self.global_cap:
            return body
        if self._is_unsafe_body(body) or self._already_has_memory_error(body):
            return body
        new_body: List[ast.stmt] = []
        wrapped = 0
        for i, stmt in enumerate(body):
            if i == 0 and self._is_docstring(stmt):
                new_body.append(stmt)
                continue
            if isinstance(stmt, self._SKIP_TYPES):
                new_body.append(stmt)
                continue
            if is_generated(stmt):
                new_body.append(stmt)
                continue
            if wrapped >= self._cap or (
                    self.global_cap is not None and self._global_wrapped[0] >= self.global_cap):
                new_body.append(stmt)
                continue
            new_body.extend(self._wrap_statement(stmt, rng))
            wrapped += 1
            self._global_wrapped[0] += 1
        return new_body
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        class _Visitor(_SkipGeneratedTransformer):
            def _process(inner_self, node, body_attr: str = "body"):
                body = getattr(node, body_attr)
                new_body = self._process_body(body, rng)
                count[0] += max(0, len(new_body) - len(body))
                setattr(node, body_attr, new_body)
                inner_self.generic_visit(node)
                return node
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                return inner_self._process(node)
            def visit_AsyncFunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                return node
            def visit_ClassDef(inner_self, node: ast.ClassDef):
                body = node.body
                new_body: List[ast.stmt] = []
                wrapped = 0
                for i, stmt in enumerate(body):
                    if i == 0 and self._is_docstring(stmt):
                        new_body.append(stmt)
                        continue
                    at_global_cap = (self.global_cap is not None
                                     and self._global_wrapped[0] >= self.global_cap)
                    if (isinstance(stmt, (ast.Assign, ast.AugAssign,
                                          ast.AnnAssign, ast.Expr))
                            and not is_generated(stmt)
                            and not self._is_unsafe_body([stmt])
                            and wrapped < self._cap and not at_global_cap):
                        new_body.extend(self._wrap_statement(stmt, rng))
                        count[0] += 1
                        wrapped += 1
                        self._global_wrapped[0] += 1
                    else:
                        new_body.append(stmt)
                node.body = new_body
                inner_self.generic_visit(node)
                return node
        _Visitor().visit(tree)
        return count[0]
def dzydkh_tautology_expr(rng: "random.Random") -> ast.expr:
    a = rng.randint(-90_000, 90_000)
    b = rng.randint(-90_000, 90_000)
    form = rng.randrange(9)
    if form == 0:
        return ast.Compare(left=ast.Constant(value=a), ops=[ast.Lt()],
                           comparators=[ast.Constant(value=a + rng.randint(1, 100_000))])
    if form == 1:
        return ast.Compare(left=ast.Call(func=ast.Name(id="len", ctx=ast.Load()),
                                         args=[ast.Call(func=ast.Name(id="str", ctx=ast.Load()),
                                                        args=[ast.Constant(value=a)], keywords=[])],
                                         keywords=[]),
                           ops=[ast.GtE()], comparators=[ast.Constant(value=1)])
    if form == 2:
        return ast.Compare(left=ast.BinOp(left=ast.Constant(value=a), op=ast.Sub(),
                                          right=ast.Constant(value=a)),
                           ops=[ast.Eq()], comparators=[ast.Constant(value=0)])
    if form == 3:
        return ast.Compare(
            left=ast.BinOp(left=ast.Constant(value=a), op=ast.Mod(), right=ast.Constant(value=2)),
            ops=[ast.In()], comparators=[ast.Tuple(elts=[ast.Constant(value=0),
                                                         ast.Constant(value=1)], ctx=ast.Load())])
    if form == 4:
        return ast.Compare(left=ast.Call(func=ast.Name(id="abs", ctx=ast.Load()),
                                         args=[ast.Constant(value=a)], keywords=[]),
                           ops=[ast.GtE()], comparators=[ast.Constant(value=0)])
    if form == 5:
        return ast.Compare(left=ast.Subscript(
            value=ast.Tuple(elts=[ast.Constant(value=a), ast.Constant(value=b)], ctx=ast.Load()),
            slice=ast.Constant(value=0), ctx=ast.Load()),
            ops=[ast.Eq()], comparators=[ast.Constant(value=a)])
    if form == 6:
        return ast.Compare(left=ast.Call(func=ast.Name(id="len", ctx=ast.Load()),
                                         args=[ast.Call(func=ast.Name(id="globals", ctx=ast.Load()),
                                                        args=[], keywords=[])],
                                         keywords=[]),
                           ops=[ast.GtE()], comparators=[ast.Constant(value=0)])
    if form == 7:
        def _idcall() -> ast.Call:
            return ast.Call(func=ast.Name(id="id", ctx=ast.Load()),
                            args=[ast.Constant(value=a)], keywords=[])
        return ast.Compare(left=ast.BinOp(left=_idcall(), op=ast.Sub(),
                                          right=_idcall()),
                           ops=[ast.Eq()], comparators=[ast.Constant(value=0)])
    _hb = rng.randbytes(4)
    def _hcall() -> ast.Call:
        return ast.Call(func=ast.Name(id="hash", ctx=ast.Load()),
                        args=[ast.Constant(value=_hb)], keywords=[])
    return ast.Compare(left=_hcall(), ops=[ast.Eq()], comparators=[_hcall()])
class OpaquePredicatePass(TransformationPass):
    name = "opaque_predicate"
    def __init__(self, expansion_level: str, build_seed: int, style: str = "wrap",
                 coverage: float = 0.9):
        self.expansion_level = expansion_level
        self.build_seed = build_seed
        self.style = style if style in ("wrap", "chain") else "wrap"
        self.coverage = min(1.0, max(0.1, coverage))
    @staticmethod
    def _collect_anchor_frames(tree: ast.Module) -> Dict[int, List[str]]:
        frames: Dict[int, List[str]] = {}
        scope_stack: List[Set[str]] = []
        def _args_of(node) -> List[str]:
            args = node.args
            out = [a.arg for a in list(args.posonlyargs)
                   + list(args.args) + list(args.kwonlyargs)]
            if args.vararg:
                out.append(args.vararg.arg)
            if args.kwarg:
                out.append(args.kwarg.arg)
            return out
        def _visit(node: ast.AST) -> None:
            is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Lambda))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                for s in scope_stack:
                    s.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                for s in scope_stack:
                    s.add(node.name)
            elif isinstance(node, ast.alias) and node.asname:
                for s in scope_stack:
                    s.add(node.asname)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                for s in scope_stack:
                    s.add(node.id)
            if is_fn:
                scope_stack.append(set())
            for child in ast.iter_child_nodes(node):
                _visit(child)
            if is_fn:
                bound = scope_stack.pop()
                argnames = _args_of(node)
                frames[id(node)] = [
                    n for n in argnames
                    if n not in bound and n not in ("self", "cls")]
        _visit(tree)
        return frames
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        outer._anchor_frames = outer._collect_anchor_frames(tree)
        class _Visitor(_SkipGeneratedTransformer):
            def __init__(inner_self):
                inner_self._anchors: List[List[str]] = []
            def _current_anchors(inner_self) -> List[str]:
                out: List[str] = []
                for frame in inner_self._anchors:
                    out.extend(frame)
                return out
            def _push_args(inner_self, node) -> None:
                inner_self._anchors.append(
                    outer._anchor_frames.get(id(node), []))
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                inner_self._push_args(node)
                inner_self.generic_visit(node)
                inner_self._anchors.pop()
                return node
            def visit_AsyncFunctionDef(inner_self, node: ast.AsyncFunctionDef):
                inner_self._push_args(node)
                inner_self.generic_visit(node)
                inner_self._anchors.pop()
                return node
            def visit_Lambda(inner_self, node: ast.Lambda):
                inner_self._push_args(node)
                inner_self.generic_visit(node)
                inner_self._anchors.pop()
                return node
            def visit_Assign(inner_self, node: ast.Assign):
                inner_self.generic_visit(node)
                if (len(node.targets) == 1
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, int)
                        and not isinstance(node.value.value, bool)):
                    if rng.random() <= outer.coverage:
                        node.value = outer._opaque_wrap(
                            node.value, rng,
                            anchors=inner_self._current_anchors())
                        count[0] += 1
                elif (len(node.targets) == 1
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and 1 <= len(node.value.value) <= 64):
                    if rng.random() <= outer.coverage * 0.5:
                        node.value = outer._opaque_wrap(
                            node.value, rng, force_chain=True,
                            anchors=inner_self._current_anchors())
                        count[0] += 1
                return node
        _Visitor().visit(tree)
        if count[0]:
            ast.fix_missing_locations(tree)
        return count[0]
    def _tautology(self, rng: "random.Random") -> ast.expr:
        return dzydkh_tautology_expr(rng)
    def _arg_identity(self, anchors: List[str],
                      rng: "random.Random") -> Optional[ast.expr]:
        if not anchors:
            return None
        a = anchors[rng.randrange(len(anchors))]
        if rng.random() < 0.5:
            node: ast.expr = ast.Compare(
                left=ast.Name(id=a, ctx=ast.Load()), ops=[ast.Is()],
                comparators=[ast.Name(id=a, ctx=ast.Load())])
        else:
            node = ast.Compare(
                left=ast.Call(func=ast.Name(id="type", ctx=ast.Load()),
                              args=[ast.Name(id=a, ctx=ast.Load())],
                              keywords=[]),
                ops=[ast.Is()],
                comparators=[ast.Call(
                    func=ast.Name(id="type", ctx=ast.Load()),
                    args=[ast.Name(id=a, ctx=ast.Load())],
                    keywords=[])])
        mark_generated(node)
        return node
    def _opaque_wrap(self, value_node: ast.Constant,
                     rng: "random.Random", force_chain: bool = False,
                     anchors: Optional[List[str]] = None) -> ast.expr:
        def _tie(test: ast.expr) -> ast.expr:
            ident = self._arg_identity(anchors or [], rng) \
                if anchors else None
            if ident is not None and rng.random() < 0.5:
                tied = ast.BoolOp(op=ast.And(), values=[test, ident])
                mark_generated(tied)
                return tied
            return test
        if self.style == "chain" or force_chain:
            depth = rng.randint(2, 4)
            expr: ast.expr = value_node
            for _ in range(depth):
                test = _tie(self._tautology(rng))
                mark_generated(test)
                expr = ast.IfExp(test=test,
                                 body=ast.copy_location(value_node, value_node),
                                 orelse=ast.copy_location(expr, expr))
            return expr
        rand_a = rng.randint(1, 10_000)
        rand_b = rng.randint(rand_a + 1, rand_a + 100_000)
        dead_val = rng.randint(-99_999, -1)
        test = ast.BoolOp(
            op=ast.And(),
            values=[
                ast.Compare(
                    left=ast.Constant(value=0),
                    ops=[ast.Lt()],
                    comparators=[ast.Constant(value=1)],
                ),
                _tie(self._tautology(rng)),
            ],
        )
        orelse = ast.Constant(value=dead_val)
        mark_generated(test)
        mark_generated(orelse)
        return ast.IfExp(test=test, body=value_node, orelse=orelse)
class BooleanAlgebraPass(TransformationPass):
    name = "boolean_algebra"
    def __init__(self, apply_probability: float = 0.6):
        self.apply_probability = apply_probability
    _CMP_INVERT = {
        ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
        ast.Lt: ast.GtE, ast.GtE: ast.Lt,
        ast.LtE: ast.Gt, ast.Gt: ast.LtE,
    }
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        prob = self.apply_probability
        parents: Dict[int, ast.AST] = {}
        for _p in ast.walk(tree):
            for _ch in ast.iter_child_nodes(_p):
                parents.setdefault(id(_ch), _p)
        def _in_condition_slot(node: ast.AST) -> bool:
            cur = node
            while True:
                p = parents.get(id(cur))
                if p is None:
                    return False
                if (isinstance(p, ast.UnaryOp) and isinstance(p.op, ast.Not)
                        and p.operand is cur):
                    cur = p
                    continue
                if isinstance(p, ast.BoolOp) and cur in p.values:
                    cur = p
                    continue
                if isinstance(p, (ast.If, ast.While)) and p.test is cur:
                    return True
                if isinstance(p, ast.Assert) and p.test is cur:
                    return True
                if isinstance(p, ast.IfExp) and p.test is cur:
                    return True
                if isinstance(p, ast.comprehension) and cur in p.ifs:
                    return True
                return False
        class _Visitor(_SkipGeneratedTransformer):
            def visit_BoolOp(inner_self, node: ast.BoolOp):
                inner_self.generic_visit(node)
                if rng.random() >= prob:
                    return node
                if not _in_condition_slot(node):
                    return node
                inverted_op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
                negated_values = [
                    ast.UnaryOp(op=ast.Not(), operand=v) for v in node.values
                ]
                new_node = ast.UnaryOp(
                    op=ast.Not(),
                    operand=ast.BoolOp(op=inverted_op, values=negated_values),
                )
                count[0] += 1
                return ast.copy_location(new_node, node)
            def visit_Compare(inner_self, node: ast.Compare):
                inner_self.generic_visit(node)
                if len(node.ops) != 1 or type(node.ops[0]) not in BooleanAlgebraPass._CMP_INVERT:
                    return node
                if rng.random() >= prob:
                    return node
                inverted_cls = BooleanAlgebraPass._CMP_INVERT[type(node.ops[0])]
                new_compare = ast.Compare(
                    left=node.left, ops=[inverted_cls()], comparators=node.comparators,
                )
                new_node = ast.UnaryOp(op=ast.Not(), operand=new_compare)
                count[0] += 1
                return ast.copy_location(new_node, node)
        _Visitor().visit(tree)
        return count[0]
_INDIRECTION_BUILTINS = {
    "chr", "ord", "len", "range", "type", "isinstance", "issubclass",
    "getattr", "setattr", "hasattr", "callable", "iter", "next",
    "map", "filter", "zip", "enumerate", "sorted", "reversed",
    "min", "max", "sum", "abs", "round", "hash", "id", "repr",
}
class BuiltinsIndirectionPass(TransformationPass):
    name = "builtins_indirection"
    def __init__(self, naming: "RuneNaming", build_seed: int):
        self.naming = naming
        self.build_seed = build_seed
        self._dict_name = naming.generate(f"bi::dict::{build_seed}")
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        dict_name = self._dict_name
        class _Replacer(_SkipGeneratedTransformer):
            def visit_Name(inner_self, node: ast.Name):
                if (isinstance(node.ctx, ast.Load)
                        and node.id in _INDIRECTION_BUILTINS):
                    count[0] += 1
                    return ast.copy_location(
                        ast.Subscript(
                            value=ast.Name(id=dict_name, ctx=ast.Load()),
                            slice=ast.Constant(value=node.id),
                            ctx=ast.Load(),
                        ),
                        node,
                    )
                return node
            def visit_Import(inner_self, node): return node
            def visit_ImportFrom(inner_self, node): return node
        _Replacer().visit(tree)
        if count[0] > 0:
            _h_bi_ind = _hidden_name_src("builtins", self.build_seed, "hidmod")
            dict_assign = ast.parse(
                f"{dict_name} = __import__({_h_bi_ind}).__dict__"
            ).body[0]
            tree.body.insert(_module_insert_index(tree), dict_assign)
            ast.fix_missing_locations(dict_assign)
            mark_generated(dict_assign)
        return count[0]
class DeadPathPass(TransformationPass):
    name = "dead_path"
    def __init__(self, naming: "RuneNaming", build_seed: int, cap: int = 40):
        self.naming = naming
        self.build_seed = build_seed
        self.cap = max(0, cap)
        self._counter = [0]
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        naming = self.naming
        class _V(_SkipGeneratedTransformer):
            def _finish(inner_self, body):
                if (not body or self.cap <= 0 or count[0] >= self.cap):
                    return body
                last = body[-1]
                if isinstance(last, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                    return body
                if is_generated(last):
                    return body
                a = rng.randint(5, 90_000)
                test = ast.Compare(left=ast.Constant(value=a), ops=[ast.Gt()],
                                   comparators=[ast.Constant(value=a)])
                mark_generated(test)
                junk = []
                for j in range(rng.randint(1, 2)):
                    nm = naming.generate(f"dp::{self._counter[0]}::{j}")
                    self._counter[0] += 1
                    st = ast.Assign(targets=[ast.Name(id=nm, ctx=ast.Store())],
                                    value=ast.Constant(value=rng.randint(1, 10**12)),
                                    lineno=0, col_offset=0)
                    mark_generated(st)
                    ast.fix_missing_locations(st)
                    junk.append(st)
                guard = ast.If(test=test, body=junk, orelse=[])
                ast.fix_missing_locations(guard)
                mark_generated(guard)
                count[0] += 1
                return body + [guard]
            def visit_FunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                node.body = inner_self._finish(node.body)
                return node
            visit_AsyncFunctionDef = visit_FunctionDef
        _V().visit(tree)
        return count[0]
_OBSIDIAN_LINK_MIN = 10 ** 50
_OBSIDIAN_LINK_MAX = 10 ** 53
_PURE_FOLD_CALLS: Dict[str, Any] = {
    "str": str, "type": type, "bool": bool, "int": int, "chr": chr,
    "len": len,
}
_WRAPABLE_STMTS = (ast.Expr, ast.Assign, ast.AugAssign, ast.Pass)
def _used_identifiers(tree: ast.AST) -> Set[str]:
    out: Set[str] = set()
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.arg):
            out.add(sub.arg)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            out.add(sub.name)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            out.add(sub.name)
        elif isinstance(sub, ast.alias) and sub.asname:
            out.add(sub.asname)
    return out
def _gen_unique(naming: RuneNaming, key: str, used: Set[str]) -> str:
    for i in range(100):
        nm = naming.generate(f"{key}@@{i}" if i else key)
        if nm not in used:
            used.add(nm)
            return nm
    i = 0
    while True:
        nm = f"_zx_{abs(hash((key, i))) % (36 ** 6):06d}"
        if nm not in used:
            used.add(nm)
            return nm
        i += 1
def _obs_link_big(rng: "random.Random") -> int:
    return rng.randint(_OBSIDIAN_LINK_MIN, _OBSIDIAN_LINK_MAX)
def _eval_pure_const(node: ast.AST) -> Tuple[bool, Any]:
    try:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or node.value is None:
                return True, node.value
            if isinstance(node.value, (int, float, str, bytes)):
                return True, node.value
            return False, None
        if isinstance(node, ast.Name):
            if node.id == "True":
                return True, True
            if node.id == "False":
                return True, False
            if node.id == "None":
                return True, None
            return False, None
        if isinstance(node, ast.BoolOp) and isinstance(
                node.op, (ast.And, ast.Or)):
            vals = []
            for v in node.values:
                ok, vv = _eval_pure_const(v)
                if not ok:
                    return False, None
                vals.append(vv)
            if isinstance(node.op, ast.And):
                res: Any = True
                for vv in vals:
                    res = res and vv
                return True, res
            res = False
            for vv in vals:
                res = res or vv
            return True, res
        if isinstance(node, ast.UnaryOp) and isinstance(
                node.op, (ast.Not, ast.USub, ast.UAdd, ast.Invert)):
            ok, vv = _eval_pure_const(node.operand)
            if not ok:
                return False, None
            if isinstance(node.op, ast.Not):
                return True, (not vv)
            if isinstance(node.op, ast.USub):
                return True, -vv
            if isinstance(node.op, ast.UAdd):
                return True, +vv
            return True, ~vv
        if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv,
                           ast.Mod, ast.Pow, ast.LShift, ast.RShift,
                           ast.BitAnd, ast.BitOr, ast.BitXor)):
            ok1, a = _eval_pure_const(node.left)
            ok2, b = _eval_pure_const(node.right)
            if not (ok1 and ok2):
                return False, None
            if isinstance(node.op, ast.Pow) and abs(int(b)) > 10:
                return False, None
            if isinstance(node.op, (ast.LShift, ast.RShift)) and not (
                    0 <= int(b) <= 512):
                return False, None
            import operator as _op
            fn = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
                  ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod,
                  ast.Pow: _op.pow, ast.LShift: _op.lshift,
                  ast.RShift: _op.rshift, ast.BitAnd: _op.and_,
                  ast.BitOr: _op.or_, ast.BitXor: _op.xor}[type(node.op)]
            return True, fn(a, b)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(
                node.ops[0], (ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
                               ast.Gt, ast.GtE, ast.In, ast.NotIn)):
            if isinstance(node.ops[0], (ast.In, ast.NotIn)):
                ok1, a = _eval_pure_const(node.left)
                if not ok1:
                    return False, None
                rhs = node.comparators[0]
                if (isinstance(rhs, ast.Call)
                        and isinstance(rhs.func, ast.Name)
                        and rhs.func.id == "range"
                        and not rhs.keywords and 1 <= len(rhs.args) <= 3):
                    rargs = []
                    for ar in rhs.args:
                        ok, vv = _eval_pure_const(ar)
                        if not ok or isinstance(vv, bool) \
                                or not isinstance(vv, int):
                            return False, None
                        rargs.append(vv)
                    try:
                        rr = range(*rargs)
                        res = (a in rr)
                    except Exception:
                        return False, None
                    return True, (not res if isinstance(
                        node.ops[0], ast.NotIn) else res)
                ok2, b = _eval_pure_const(rhs)
                if not ok2:
                    return False, None
                try:
                    res = (a in b)
                except Exception:
                    return False, None
                return True, (not res if isinstance(
                    node.ops[0], ast.NotIn) else res)
            ok1, a = _eval_pure_const(node.left)
            ok2, b = _eval_pure_const(node.comparators[0])
            if not (ok1 and ok2):
                return False, None
            import operator as _op
            fn = {ast.Eq: _op.eq, ast.NotEq: _op.ne, ast.Lt: _op.lt,
                  ast.LtE: _op.le, ast.Gt: _op.gt,
                  ast.GtE: _op.ge}[type(node.ops[0])]
            return True, fn(a, b)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _PURE_FOLD_CALLS and not node.keywords:
            fargs = []
            for a in node.args:
                ok, vv = _eval_pure_const(a)
                if not ok:
                    return False, None
                fargs.append(vv)
            return True, _PURE_FOLD_CALLS[node.func.id](*fargs)
        return False, None
    except Exception:
        return False, None
def _mkcall(func_name: str, args: List[ast.expr]) -> ast.Call:
    return ast.Call(func=ast.Name(id=func_name, ctx=ast.Load()),
                    args=list(args), keywords=[])
def _obs_link_test(rng: "random.Random",
                   want_true: bool) -> Optional[ast.expr]:
    for _ in range(8):
        c0 = rng.randint(0, 2000)
        c1 = rng.randint(0, 2000)
        c2 = rng.randint(0, 2000)
        if want_true:
            rhs: ast.expr = ast.Compare(
                left=ast.Constant(value=c1), ops=[ast.Gt()],
                comparators=[ast.Constant(value=c2)])
        else:
            rhs = ast.BinOp(left=ast.Constant(value=c1), op=ast.Add(),
                            right=ast.Constant(value=c2))
        test = ast.Compare(
            left=_mkcall("str", [_mkcall("type", [_mkcall(
                "bool", [ast.Constant(value=c0)])])]),
            ops=[ast.Eq()],
            comparators=[_mkcall("str", [_mkcall("type", [rhs])])])
        ok, val = _eval_pure_const(test)
        if ok and bool(val) is want_true:
            mark_generated(test)
            ast.fix_missing_locations(test)
            return test
    return None
def _straight_runs(body: List[ast.stmt]) -> List[Tuple[int, int]]:
    runs: List[Tuple[int, int]] = []
    i, n = 0, len(body)
    while i < n:
        if not isinstance(body[i], _WRAPABLE_STMTS) or is_generated(body[i]):
            i += 1
            continue
        j = i
        while j < n and isinstance(body[j], _WRAPABLE_STMTS) \
                and not is_generated(body[j]):
            j += 1
        if j - i >= 2:
            runs.append((i, j))
        i = j
    return runs
def _binds_builtins(func: ast.AST) -> bool:
    for sub in ast.walk(func):
        if isinstance(sub, ast.Name) and isinstance(
                sub.ctx, ast.Store) and sub.id in (
                "str", "type", "bool", "int", "chr", "len", "range"):
            return True
        if isinstance(sub, ast.arg) and sub.arg in (
                "str", "type", "bool", "int", "chr", "len", "range"):
            return True
    return False
def _obs_link_test_env(rng: "random.Random", want_true: bool,
                       items: List[Tuple[str, int]],
                       preferred: Optional[List[str]] = None
                       ) -> Optional[ast.expr]:
    def _subst(node: ast.expr) -> Optional[ast.expr]:
        import copy
        class _Sub(ast.NodeTransformer):
            def visit_Name(self, nd: ast.Name):
                if isinstance(nd.ctx, ast.Load) and nd.id in _env:
                    return ast.copy_location(ast.Constant(value=_env[nd.id]),
                                             nd)
                return nd
        _env = {n: v for (n, v) in items}
        try:
            return _Sub().visit(copy.deepcopy(node))
        except Exception:
            return None
    def _check(node: ast.expr) -> Optional[ast.expr]:
        sub = _subst(node)
        if sub is None:
            return None
        ok, val = _eval_pure_const(sub)
        if ok and bool(val) is want_true:
            mark_generated(node)
            ast.fix_missing_locations(node)
            return node
        return None
    def _nm(nm: str) -> ast.Name:
        return ast.Name(id=nm, ctx=ast.Load())
    def _cc(vv: int) -> ast.Constant:
        return ast.Constant(value=vv)
    _pset = set(preferred or [])
    _first = [(n, v) for (n, v) in items if n in _pset]
    _rest = [(n, v) for (n, v) in items if n not in _pset]
    for _round, _items in enumerate(
            [_first, _rest] if _first else [_rest]):
        cands: List[ast.expr] = []
        for (tn, tv) in _items:
            if want_true:
                cands.append(ast.Compare(
                    left=_nm(tn), ops=[ast.Eq()], comparators=[_cc(tv)]))
            else:
                cands.append(ast.Compare(
                    left=_nm(tn), ops=[ast.Eq()],
                    comparators=[_cc(tv + rng.randint(1, 100))]))
        if len(_items) >= 1:
            tn, tv = _items[rng.randrange(len(_items))]
            c = rng.randint(0, 5000)
            if want_true:
                cands.append(ast.Compare(
                    left=_mkcall("str", [_mkcall(
                        "type", [ast.BinOp(left=_nm(tn), op=ast.Sub(),
                                           right=_cc(c))])]),
                    ops=[ast.Eq()],
                    comparators=[_mkcall("str", [_mkcall("type", [_cc(0)])])]))
            else:
                cands.append(ast.Compare(
                    left=_mkcall("str", [_mkcall(
                        "type", [ast.BinOp(left=_nm(tn), op=ast.Sub(),
                                           right=_cc(c))])]),
                    ops=[ast.Eq()],
                    comparators=[_mkcall("str", [_mkcall("type", [_cc("s")])])]))
        if len(_items) >= 2:
            (a, va), (b, vb) = rng.sample(_items, 2)
            if va != vb:
                lo, hi = (a, b) if va < vb else (b, a)
                if want_true:
                    cands.append(ast.Compare(
                        left=_nm(lo), ops=[ast.Lt()],
                        comparators=[_nm(hi)]))
                else:
                    cands.append(ast.Compare(
                        left=_nm(lo), ops=[ast.Gt()],
                        comparators=[_nm(hi)]))
        if len(_items) >= 1:
            tn, tv = _items[rng.randrange(len(_items))]
            _m = rng.randint(257, 4096)
            _a = rng.randint(2, 255)
            _b = rng.randint(0, 10000)
            _res = (tv * _a + _b) % _m
            if want_true:
                cands.append(ast.Compare(
                    left=ast.BinOp(
                        left=ast.BinOp(
                            left=ast.BinOp(left=_nm(tn), op=ast.Mult(),
                                           right=_cc(_a)),
                            op=ast.Add(), right=_cc(_b)),
                        op=ast.Mod(), right=_cc(_m)),
                    ops=[ast.Eq()], comparators=[_cc(_res)]))
            else:
                cands.append(ast.Compare(
                    left=ast.BinOp(
                        left=ast.BinOp(
                            left=ast.BinOp(left=_nm(tn), op=ast.Mult(),
                                           right=_cc(_a)),
                            op=ast.Add(), right=_cc(_b)),
                        op=ast.Mod(), right=_cc(_m)),
                    ops=[ast.Eq()],
                    comparators=[_cc((_res + rng.randint(1, _m - 1)) % _m)]))
        if len(_items) >= 2:
            (a, va), (b, vb) = rng.sample(_items, 2)
            _delta = va - vb
            if want_true:
                cands.append(ast.Compare(
                    left=ast.BinOp(left=_nm(a), op=ast.Sub(),
                                   right=_nm(b)),
                    ops=[ast.Eq()], comparators=[_cc(_delta)]))
            else:
                cands.append(ast.Compare(
                    left=ast.BinOp(left=_nm(a), op=ast.Sub(),
                                   right=_nm(b)),
                    ops=[ast.Eq()],
                    comparators=[_cc(_delta + rng.randint(1, 500))]))
        if len(_items) >= 1:
            tn, tv = _items[rng.randrange(len(_items))]
            if want_true:
                _lo = tv - rng.randint(1, 500)
                _hi = tv + rng.randint(1, 500)
            elif rng.random() < 0.5:
                _lo = tv + rng.randint(1, 500)
                _hi = _lo + rng.randint(1, 500)
            else:
                _hi = tv - rng.randint(1, 500)
                _lo = _hi - rng.randint(1, 500)
            cands.append(ast.Compare(
                left=_nm(tn), ops=[ast.In()],
                comparators=[ast.Call(
                    func=ast.Name(id="range", ctx=ast.Load()),
                    args=[_cc(_lo), _cc(_hi)], keywords=[])]))
        if len(_items) >= 1:
            tn, tv = _items[rng.randrange(len(_items))]
            _sq = ast.BinOp(
                left=ast.BinOp(left=_nm(tn), op=ast.Sub(), right=_cc(tv)),
                op=ast.Mult(),
                right=ast.BinOp(left=_nm(tn), op=ast.Sub(), right=_cc(tv)))
            if want_true:
                cands.append(ast.Compare(
                    left=_sq, ops=[ast.Eq()], comparators=[_cc(0)]))
            else:
                cands.append(ast.Compare(
                    left=_sq, ops=[ast.Eq()],
                    comparators=[_cc(rng.randint(1, 10 ** 6))]))
        rng.shuffle(cands)
        for cand in cands:
            hit = _check(cand)
            if hit is not None:
                return hit
    return None
class ChainLinkPass(TransformationPass):
    name = "chain_link"
    def __init__(self, naming: RuneNaming, build_seed: int,
                 coverage: float = 0.5, max_links: int = 6):
        self.naming = naming
        self.build_seed = build_seed
        self.coverage = min(1.0, max(0.0, coverage))
        self.max_links = max(1, int(max_links))
        self._ctr = [0]
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        naming = self.naming
        used = _used_identifiers(tree)
        def fresh() -> str:
            nm = _gen_unique(
                naming, f"chain::{self.build_seed}::{self._ctr[0]}", used)
            self._ctr[0] += 1
            return nm
        def link_value(prev: Optional[Tuple[str, int]]
                       ) -> Tuple[ast.expr, int]:
            a = _obs_link_big(rng)
            b = rng.randint(1, 10 ** 6)
            k = rng.randint(1, 61)
            if prev is None or rng.random() < 0.5:
                return (ast.BinOp(
                    left=ast.BinOp(left=ast.Constant(value=a),
                                   op=ast.LShift(),
                                   right=ast.Constant(value=k)),
                    op=ast.BitXor(), right=ast.Constant(value=b)),
                    ((a << k) ^ b))
            _pn, _pv = prev
            return (ast.BinOp(
                left=ast.BinOp(left=ast.Name(id=_pn, ctx=ast.Load()),
                               op=ast.BitXor(),
                               right=ast.Constant(value=a)),
                op=ast.Add(), right=ast.Constant(value=b)),
                ((_pv ^ a) + b))
        class _V(_SkipGeneratedTransformer):
            def _process(inner_self, body: List[ast.stmt],
                         allow_wrap: bool) -> None:
                defs: List[Tuple[ast.stmt, str, int]] = []
                for (a, b) in reversed(_straight_runs(body)):
                    if rng.random() > self.coverage:
                        continue
                    prev: Optional[Tuple[str, int]] = None
                    n_links = min(self.max_links, max(1, b - a - 1))
                    pos = a + 1
                    loops_done = [0]
                    for _ in range(n_links):
                        if pos >= len(body):
                            break
                        if loops_done[0] < 2 and allow_wrap \
                                and rng.random() < 0.5:
                            nm = fresh()
                            c0 = rng.randint(10 ** 12, 10 ** 15)
                            c1 = rng.randint(1, 10 ** 9)
                            c2 = rng.randint(1, 10 ** 6)
                            kk = rng.randint(3, 7)
                            vv = c0
                            for _ in range(kk):
                                vv = (vv ^ c1) + c2
                            iv = fresh()
                            init = ast.Assign(
                                targets=[ast.Name(id=nm, ctx=ast.Store())],
                                value=ast.Constant(value=c0))
                            loop = ast.For(
                                target=ast.Name(id=iv, ctx=ast.Store()),
                                iter=ast.Call(
                                    func=ast.Name(id="range", ctx=ast.Load()),
                                    args=[ast.Constant(value=kk)],
                                    keywords=[]),
                                body=[ast.Assign(
                                    targets=[ast.Name(id=nm, ctx=ast.Store())],
                                    value=ast.BinOp(
                                        left=ast.BinOp(
                                            left=ast.Name(
                                                id=nm, ctx=ast.Load()),
                                            op=ast.BitXor(),
                                            right=ast.Constant(value=c1)),
                                        op=ast.Add(),
                                        right=ast.Constant(value=c2)))],
                                orelse=[])
                            mark_generated(init)
                            mark_generated(loop)
                            ast.fix_missing_locations(init)
                            ast.fix_missing_locations(loop)
                            body.insert(pos, init)
                            body.insert(pos + 1, loop)
                            defs.append((loop, nm, vv, True))
                            prev = (nm, vv)
                            pos += 3
                            loops_done[0] += 1
                            count[0] += 2
                            continue
                        nm = fresh()
                        node, val = link_value(prev)
                        st = ast.Assign(
                            targets=[ast.Name(id=nm, ctx=ast.Store())],
                            value=node)
                        mark_generated(st)
                        ast.fix_missing_locations(st)
                        body.insert(pos, st)
                        defs.append((st, nm, val, False))
                        prev = (nm, val)
                        pos += 2
                        count[0] += 1
                    if defs and rng.random() < 0.6:
                        _loops = [d for d in defs if d[3]]
                        _pool = _loops if _loops and rng.random() < 0.7 \
                            else defs
                        _st3, _cn, _cv, _cl = _pool[
                            rng.randrange(len(_pool))]
                        _dc = _mkcall(rng.choice(["int", "str", "bool"]),
                                      [ast.Name(id=_cn, ctx=ast.Load())])
                        _de = ast.Expr(value=_dc)
                        mark_generated(_de)
                        ast.fix_missing_locations(_de)
                        try:
                            _ip = body.index(_st3) + 1
                        except ValueError:
                            _ip = len(body)
                        body.insert(min(_ip, len(body)), _de)
                        count[0] += 1
                    if not allow_wrap:
                        continue
                    end = len(body)
                    cands = [kk for kk in range(a, end)
                             if kk < len(body) and not is_generated(body[kk])
                             and isinstance(body[kk], _WRAPABLE_STMTS)]
                    rng.shuffle(cands)
                    for kk in cands[:3]:
                        if rng.random() > 0.7:
                            continue
                        elig = []
                        pref = []
                        for (_ss, _nn, _vv, _lp) in defs:
                            try:
                                if body.index(_ss) < kk:
                                    elig.append((_nn, _vv))
                                    if _lp:
                                        pref.append(_nn)
                            except ValueError:
                                continue
                        if not elig:
                            continue
                        want_true = rng.random() < 0.6
                        test = _obs_link_test_env(rng, want_true, elig,
                                                  pref or None)
                        if test is None:
                            continue
                        orig = body[kk]
                        if want_true:
                            new: ast.stmt = ast.If(
                                test=test, body=[orig], orelse=[ast.Pass()])
                        else:
                            new = ast.If(
                                test=test, body=[ast.Pass()], orelse=[orig])
                        mark_generated(new)
                        ast.fix_missing_locations(new)
                        body[kk] = new
                        count[0] += 1
            def _func(inner_self, node: ast.AST) -> ast.AST:
                inner_self.generic_visit(node)
                allow_wrap = not _binds_builtins(node)
                for field in ("body", "orelse", "finalbody"):
                    lst = getattr(node, field, None)
                    if isinstance(lst, list) and lst and all(
                            isinstance(x, ast.stmt) for x in lst):
                        inner_self._process(lst, allow_wrap)
                return node
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                return inner_self._func(node)
            def visit_AsyncFunctionDef(inner_self, node: ast.AsyncFunctionDef):
                return inner_self._func(node)
            def visit_Module(inner_self, node: ast.Module):
                return inner_self._func(node)
            def visit_ClassDef(inner_self, node: ast.ClassDef):
                return inner_self._func(node)
            def visit_If(inner_self, node: ast.If):
                return inner_self._func(node)
            def visit_While(inner_self, node: ast.While):
                return inner_self._func(node)
            def visit_For(inner_self, node: ast.For):
                return inner_self._func(node)
            def visit_AsyncFor(inner_self, node: ast.AsyncFor):
                return inner_self._func(node)
            def visit_Try(inner_self, node: ast.Try):
                return inner_self._func(node)
            def visit_With(inner_self, node: ast.With):
                return inner_self._func(node)
            def visit_ExceptHandler(inner_self, node: ast.ExceptHandler):
                inner_self.generic_visit(node)
                if isinstance(node.body, list) and node.body:
                    inner_self._process(node.body,
                                        allow_wrap=True)
                return node
        _V().visit(tree)
        return count[0]
class HandlerEmbedPass(TransformationPass):
    name = "handler_embed"
    def __init__(self, naming: RuneNaming, build_seed: int,
                 coverage: float = 0.4, cap: int = 96, group: int = 4):
        self.naming = naming
        self.build_seed = build_seed
        self.coverage = min(1.0, max(0.0, coverage))
        self.cap = max(0, int(cap))
        self.group = max(1, int(group))
        self._exc: Optional[str] = None
    def _exc_name(self, used: Optional[Set[str]] = None) -> str:
        if self._exc is None:
            if used is None:
                used = set()
            self._exc = _gen_unique(self.naming, "handler_embed::exc", used)
        return self._exc
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        used = _used_identifiers(tree)
        class _V(_SkipGeneratedTransformer):
            def _process(inner_self, body: List[ast.stmt]) -> None:
                if count[0] >= self.cap:
                    return
                for (a, b) in reversed(_straight_runs(body)):
                    if count[0] >= self.cap:
                        return
                    if rng.random() > self.coverage:
                        continue
                    take = min(self.group, b - a)
                    if take <= 0:
                        continue
                    grp = body[a:a + take]
                    tok = rng.randint(1, 10 ** 6)
                    rs = ast.Raise(
                        exc=_mkcall(self._exc_name(used),
                                    [ast.Constant(value=tok)]),
                        cause=None)
                    mark_generated(rs)
                    ast.fix_missing_locations(rs)
                    new = ast.Try(
                        body=[rs],
                        handlers=[ast.ExceptHandler(
                            type=ast.Name(id=self._exc_name(used),
                                          ctx=ast.Load()),
                            body=grp)],
                        orelse=[], finalbody=[])
                    ast.fix_missing_locations(new)
                    body[a:a + take] = [new]
                    count[0] += 1
                    return
            def _wrap(inner_self, node: ast.AST) -> ast.AST:
                inner_self.generic_visit(node)
                for field in ("body", "orelse", "finalbody"):
                    lst = getattr(node, field, None)
                    if isinstance(lst, list) and lst and all(
                            isinstance(x, ast.stmt) for x in lst):
                        inner_self._process(lst)
                return node
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                return inner_self._wrap(node)
            def visit_AsyncFunctionDef(inner_self, node: ast.AsyncFunctionDef):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Await):
                        inner_self.generic_visit(node)
                        return node
                return inner_self._wrap(node)
            def visit_Module(inner_self, node: ast.Module):
                return inner_self._wrap(node)
            def visit_ClassDef(inner_self, node: ast.ClassDef):
                return inner_self._wrap(node)
            def visit_If(inner_self, node: ast.If):
                return inner_self._wrap(node)
            def visit_While(inner_self, node: ast.While):
                return inner_self._wrap(node)
            def visit_For(inner_self, node: ast.For):
                return inner_self._wrap(node)
            def visit_Try(inner_self, node: ast.Try):
                return inner_self._wrap(node)
            def visit_With(inner_self, node: ast.With):
                return inner_self._wrap(node)
            def visit_ExceptHandler(inner_self, node: ast.ExceptHandler):
                inner_self.generic_visit(node)
                if isinstance(node.body, list) and node.body:
                    inner_self._process(node.body)
                return node
        _V().visit(tree)
        if count[0] > 0:
            cls = ast.ClassDef(
                name=self._exc_name(used), bases=[ast.Name(id="Exception",
                                                       ctx=ast.Load())],
                keywords=[], body=[ast.Pass()], decorator_list=[])
            mark_generated(cls)
            ast.fix_missing_locations(cls)
            tree.body.insert(_module_insert_index(tree), cls)
        return count[0]
def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))
class DecompilerTrapsPass(TransformationPass):
    name = "decompiler_traps"
    def __init__(self, naming: RuneNaming, build_seed: int,
                 coverage: float = 0.5, cap: int = 64, group: int = 6):
        self.naming = naming
        self.build_seed = build_seed
        self.coverage = min(1.0, max(0.0, coverage))
        self.cap = max(0, int(cap))
        self.group = max(1, int(group))
        self._ctr = [0]
        self._exc: Optional[str] = None
    def _exc_name(self, used: Set[str]) -> str:
        if self._exc is None:
            self._exc = _gen_unique(self.naming, "dectrap::exc", used)
        return self._exc
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        made = [0]
        naming = self.naming
        used = _used_identifiers(tree)
        def fresh(suffix: str) -> str:
            nm = _gen_unique(
                naming, f"dectrap::{self.build_seed}::{suffix}"
                f"::{self._ctr[0]}", used)
            self._ctr[0] += 1
            return nm
        def const_assign(nm: str, val: int) -> ast.Assign:
            st = ast.Assign(targets=[ast.Name(id=nm, ctx=ast.Store())],
                            value=ast.Constant(value=val))
            mark_generated(st)
            ast.fix_missing_locations(st)
            return st
        class _V(_SkipGeneratedTransformer):
            def _scope_ok(inner_self, node: ast.AST) -> bool:
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Yield, ast.YieldFrom,
                                        ast.Await)):
                        return False
                return True
            def _process_func(inner_self, func: ast.AST,
                              body: List[ast.stmt]) -> None:
                if not inner_self._scope_ok(func):
                    return
                runs = [r for r in _straight_runs(body) if r[1] - r[0] >= 1]
                if not runs:
                    return
                pool: List[Tuple[str, int]] = []
                if rng.random() > self.coverage and len(runs) < 2:
                    return
                _pa = 1 if body and _is_docstring_stmt(body[0]) else 0
                for _pi in range(2):
                    nm = fresh(f"pool{_pi}")
                    if not pool:
                        pv = int(rng.randint(10 ** 12, 10 ** 15))
                    else:
                        pv = int((pool[-1][1] ^ rng.randint(1, 10 ** 9))
                                 + rng.randint(1, 10 ** 6))
                    body.insert(_pa, const_assign(nm, pv))
                    pool.append((nm, pv))
                    _pa += 1
                    count[0] += 1
                _shift = 2
                for (a, b) in reversed(runs):
                    if count[0] >= self.cap:
                        return
                    if rng.random() > self.coverage:
                        continue
                    a2, b2 = a + _shift, b + _shift
                    take = min(self.group, b2 - a2)
                    if take <= 0 or a2 >= len(body):
                        continue
                    grp = body[a2:a2 + take]
                    _pn, _pv = pool[rng.randrange(len(pool))]
                    _cc = rng.randint(1, 5000)
                    _tok = ast.BinOp(
                        left=ast.BinOp(
                            left=ast.Name(id=_pn, ctx=ast.Load()),
                            op=ast.Add(), right=ast.Constant(value=_cc)),
                        op=ast.Mod(), right=ast.Constant(value=997))
                    _K = int((_pv + _cc) % 997)
                    _ok = False
                    try:
                        import copy as _copy
                        class _SubT(ast.NodeTransformer):
                            def visit_Name(self, nd: ast.Name):
                                if isinstance(nd.ctx, ast.Load):
                                    for (_qn, _qv) in pool:
                                        if nd.id == _qn:
                                            return ast.copy_location(
                                                ast.Constant(value=_qv), nd)
                                return nd
                        _tok_sub = _SubT().visit(_copy.deepcopy(_tok))
                        _chk = _eval_pure_const(_tok_sub)
                        if _chk[0]:
                            _ok = bool(int(_chk[1]) == _K)
                    except Exception:
                        _ok = False
                    if not _ok:
                        continue
                    _en = fresh("excvar")
                    _decoys = []
                    for _ in range(rng.randint(1, 3)):
                        _dk = (_K + rng.randint(1, 50)) % 997
                        if _dk == _K:
                            continue
                        _dn = fresh("decoy")
                        _decoys.append(ast.match_case(
                            pattern=ast.MatchValue(
                                value=ast.Constant(value=_dk)),
                            body=[const_assign(
                                _dn, rng.randint(10 ** 12, 10 ** 18))]))
                    _wild = [const_assign(fresh("wild"), rng.randint(
                        10 ** 12, 10 ** 18)) for _ in range(
                            rng.randint(1, 2))]
                    new = ast.Try(
                        body=[ast.Raise(
                            exc=ast.Call(
                                func=ast.Name(
                                    id=self._exc_name(used),
                                    ctx=ast.Load()),
                                args=[_tok], keywords=[]),
                            cause=None)],
                        handlers=[ast.ExceptHandler(
                            type=ast.Name(id=self._exc_name(used),
                                          ctx=ast.Load()),
                            name=_en,
                            body=[ast.Match(
                                subject=ast.Subscript(
                                    value=ast.Attribute(
                                        value=ast.Name(id=_en, ctx=ast.Load()),
                                        attr="args", ctx=ast.Load()),
                                    slice=ast.Constant(value=0),
                                    ctx=ast.Load()),
                                cases=[ast.match_case(
                                    pattern=ast.MatchValue(
                                        value=ast.Constant(value=_K)),
                                    body=list(grp))] + _decoys + [
                                    ast.match_case(
                                        pattern=ast.MatchAs(),
                                        body=_wild)])])],
                        orelse=[], finalbody=[])
                    mark_generated(new)
                    ast.fix_missing_locations(new)
                    body[a2:a2 + take] = [new]
                    count[0] += 1
                    made[0] += 1
                return
            def _top(inner_self, node: ast.AST,
                     body: List[ast.stmt]) -> ast.AST:
                inner_self.generic_visit(node)
                inner_self._process_func(node, body)
                return node
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                return inner_self._top(node, node.body)
            def visit_AsyncFunctionDef(inner_self, node: ast.AsyncFunctionDef):
                return inner_self._top(node, node.body)
            def visit_Module(inner_self, node: ast.Module):
                return inner_self._top(node, node.body)
            def visit_ClassDef(inner_self, node: ast.ClassDef):
                return inner_self._top(node, node.body)
        _V().visit(tree)
        if made[0] > 0:
            cls = ast.ClassDef(
                name=self._exc_name(used), bases=[ast.Name(id="Exception",
                                                           ctx=ast.Load())],
                keywords=[], body=[ast.Pass()], decorator_list=[])
            mark_generated(cls)
            ast.fix_missing_locations(cls)
            tree.body.insert(_module_insert_index(tree), cls)
        return count[0]
class DeadBloatPass(TransformationPass):
    name = "dead_bloat"
    def __init__(self, naming: RuneNaming, build_seed: int,
                 ratio: float = 0.15, cap: int = 120):
        self.naming = naming
        self.build_seed = build_seed
        self.ratio = min(1.0, max(0.0, ratio))
        self.cap = max(0, int(cap))
        self._ctr = [0]
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        naming = self.naming
        used = _used_identifiers(tree)
        def fresh() -> str:
            nm = _gen_unique(
                naming, f"bloat::{self.build_seed}::{self._ctr[0]}", used)
            self._ctr[0] += 1
            return nm
        def junk_assign() -> ast.Assign:
            st = ast.Assign(
                targets=[ast.Name(id=fresh(), ctx=ast.Store())],
                value=ast.Constant(value=rng.randint(10 ** 12, 10 ** 18)))
            mark_generated(st)
            ast.fix_missing_locations(st)
            return st
        class _V(_SkipGeneratedTransformer):
            def _process(inner_self, body: List[ast.stmt]) -> None:
                if count[0] >= self.cap or rng.random() > self.ratio:
                    return
                terms = [i for i, st in enumerate(body)
                         if isinstance(st, (ast.Return, ast.Raise,
                                            ast.Break, ast.Continue))]
                if not terms:
                    return
                at = terms[0] + 1
                seg: List[ast.stmt] = [junk_assign()
                                       for _ in range(rng.randint(1, 2))]
                test = _obs_link_test(rng, False)
                if test is not None:
                    arm = [junk_assign() for _ in range(rng.randint(1, 2))]
                    gd: ast.stmt = ast.If(test=test, body=arm, orelse=[])
                    mark_generated(gd)
                    ast.fix_missing_locations(gd)
                    seg.append(gd)
                for st in seg:
                    mark_generated(st)
                body[at:at] = seg
                count[0] += 1
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                inner_self.generic_visit(node)
                inner_self._process(node.body)
                return node
            def visit_AsyncFunctionDef(inner_self, node: ast.AsyncFunctionDef):
                inner_self.generic_visit(node)
                inner_self._process(node.body)
                return node
        _V().visit(tree)
        return count[0]
class LambdaThunkPass(_SkipGeneratedTransformer):
    name = "lambda_thunk"
    def __init__(self, naming: "RuneNaming", build_seed: int,
                 coverage: float = 0.35, cap: int = 140):
        self.naming = naming
        self.rng = _seeded_rng(build_seed, "lambda_thunk")
        self.coverage = min(0.95, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
    @staticmethod
    def _unsafe_arg(node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Yield, ast.YieldFrom, ast.Await,
                                ast.NamedExpr, ast.Starred)):
                return True
        return False
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        class _V(_SkipGeneratedTransformer):
            def visit_Call(inner_self, node: ast.Call):
                inner_self.generic_visit(node)
                if outer._done[0] >= outer.cap:
                    return node
                new_args = []
                changed = False
                for a in node.args:
                    if (not isinstance(a, ast.Starred)
                            and not is_generated(a)
                            and not LambdaThunkPass._unsafe_arg(a)
                            and outer.rng.random() <= outer.coverage):
                        param = outer.naming.generate(
                            f"thunk::{outer._done[0]}")
                        lam = ast.Lambda(
                            args=ast.arguments(
                                posonlyargs=[], args=[ast.arg(arg=param)],
                                kwonlyargs=[], kw_defaults=[], defaults=[],
                                vararg=None, kwarg=None),
                            body=ast.Name(id=param, ctx=ast.Load()),
                        )
                        mark_generated(lam)
                        wrapper = ast.Call(func=lam, args=[a], keywords=[])
                        ast.copy_location(wrapper, a)
                        ast.fix_missing_locations(wrapper)
                        new_args.append(wrapper)
                        outer._done[0] += 1
                        count[0] += 1
                        changed = True
                    else:
                        new_args.append(a)
                if changed:
                    node.args = new_args
                return node
        _V().visit(tree)
        return count[0]
class TautologyGuardPass(_SkipGeneratedTransformer):
    name = "tautology_guard"
    def __init__(self, build_seed: int, coverage: float = 0.45, cap: int = 200):
        self.rng = _seeded_rng(build_seed, "tautology_guard")
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
    def _guard(self, test: ast.expr) -> ast.expr:
        taut = dzydkh_tautology_expr(self.rng)
        mark_generated(taut)
        wrapped = ast.BoolOp(op=ast.And(), values=[test, taut])
        ast.copy_location(wrapped, test)
        return wrapped
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        class _V(_SkipGeneratedTransformer):
            def visit_If(inner_self, node: ast.If):
                inner_self.generic_visit(node)
                if (outer._done[0] < outer.cap
                        and not is_generated(node.test)
                        and outer.rng.random() <= outer.coverage):
                    node.test = outer._guard(node.test)
                    ast.fix_missing_locations(node.test)
                    outer._done[0] += 1
                    count[0] += 1
                return node
            def visit_While(inner_self, node: ast.While):
                inner_self.generic_visit(node)
                if (outer._done[0] < outer.cap
                        and not is_generated(node.test)
                        and outer.rng.random() <= outer.coverage):
                    node.test = outer._guard(node.test)
                    ast.fix_missing_locations(node.test)
                    outer._done[0] += 1
                    count[0] += 1
                return node
        _V().visit(tree)
        return count[0]
class ConditionSwapPass(_SkipGeneratedTransformer):
    name = "condition_swap"
    def __init__(self, build_seed: int, coverage: float = 0.4, cap: int = 160):
        self.rng = _seeded_rng(build_seed, "condition_swap")
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        class _V(_SkipGeneratedTransformer):
            def visit_If(inner_self, node: ast.If):
                inner_self.generic_visit(node)
                if (outer._done[0] < outer.cap
                        and node.orelse
                        and not is_generated(node)
                        and not is_generated(node.test)
                        and outer.rng.random() <= outer.coverage):
                    inverted = ast.UnaryOp(op=ast.Not(), operand=node.test)
                    mark_generated(inverted)
                    node.test = inverted
                    node.body, node.orelse = node.orelse, node.body
                    ast.fix_missing_locations(node)
                    outer._done[0] += 1
                    count[0] += 1
                return node
        _V().visit(tree)
        return count[0]
class EntryGuardPass(_SkipGeneratedTransformer):
    name = "entry_guard"
    def __init__(self, build_seed: int, coverage: float = 0.8, cap: int = 120):
        self.rng = _seeded_rng(build_seed, "entry_guard")
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        class _V(_SkipGeneratedTransformer):
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                inner_self.generic_visit(node)
                if (outer._done[0] >= outer.cap
                        or not node.body
                        or is_generated(node)
                        or outer.rng.random() > outer.coverage):
                    return node
                idx = 0
                if (isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    idx = 1
                if len(node.body) <= idx:
                    return node
                test = dzydkh_tautology_expr(outer.rng)
                mark_generated(test)
                guard = ast.If(test=test, body=node.body[idx:], orelse=[])
                ast.copy_location(guard, node.body[idx])
                ast.fix_missing_locations(guard)
                node.body = node.body[:idx] + [guard]
                outer._done[0] += 1
                count[0] += 1
                return node
            def visit_AsyncFunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                return node
        _V().visit(tree)
        return count[0]
class MatchFlattenPass(_SkipGeneratedTransformer):
    name = "match_flatten"
    def __init__(self, naming: "RuneNaming", build_seed: int,
                 coverage: float = 0.5, cap: int = 60):
        self.naming = naming
        self.build_seed = build_seed
        self.rng = _seeded_rng(build_seed, "match_flatten")
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
        self._ctr = [0]
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        used = _used_identifiers(tree)
        def fresh() -> str:
            nm = _gen_unique(
                outer.naming, f"matchflat::{outer.build_seed}::{outer._ctr[0]}",
                used)
            outer._ctr[0] += 1
            return nm
        class _V(_SkipGeneratedTransformer):
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                inner_self.generic_visit(node)
                if (outer._done[0] >= outer.cap
                        or is_generated(node)
                        or outer.rng.random() > outer.coverage):
                    return node
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Yield, ast.YieldFrom, ast.Await)):
                        return node
                idx = 0
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    idx = 1
                blocks = node.body[idx:]
                if len(blocks) < 3:
                    return node
                st = fresh()
                n = len(blocks)
                order = list(range(n))
                outer.rng.shuffle(order)
                cases = []
                for i in order:
                    nxt = i + 1 if i + 1 < n else -1
                    case_body = [blocks[i], ast.Assign(
                        targets=[ast.Name(id=st, ctx=ast.Store())],
                        value=ast.Constant(value=nxt))]
                    cases.append(ast.match_case(
                        pattern=ast.MatchValue(
                            value=ast.Constant(value=i)),
                        body=case_body))
                junk = ast.Expr(value=ast.BinOp(
                    left=ast.Constant(value=outer.rng.randint(10 ** 6, 10 ** 9)),
                    op=ast.BitXor(),
                    right=ast.Constant(value=outer.rng.randint(10 ** 6, 10 ** 9))))
                mark_generated(junk)
                cases.append(ast.match_case(
                    pattern=ast.MatchAs(),
                    body=[junk, ast.Assign(
                        targets=[ast.Name(id=st, ctx=ast.Store())],
                        value=ast.Constant(value=-1))]))
                match = ast.Match(
                    subject=ast.Name(id=st, ctx=ast.Load()),
                    cases=cases)
                loop = ast.While(
                    test=ast.Compare(
                        left=ast.Name(id=st, ctx=ast.Load()),
                        ops=[ast.NotEq()],
                        comparators=[ast.Constant(value=-1)]),
                    body=[match], orelse=[])
                init = ast.Assign(
                    targets=[ast.Name(id=st, ctx=ast.Store())],
                    value=ast.Constant(value=0))
                for nd in (init, loop):
                    mark_generated(nd)
                ast.fix_missing_locations(init)
                ast.fix_missing_locations(loop)
                node.body = node.body[:idx] + [init, loop]
                outer._done[0] += 1
                count[0] += 1
                return node
            def visit_AsyncFunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                return node
        _V().visit(tree)
        return count[0]
class ResidualVaultPass(TransformationPass):
    name = "residual_vault"
    def __init__(self, naming: "RuneNaming", build_seed: int,
                 coverage: float = 0.6, cap: int = 512):
        self.naming = naming
        self.build_seed = build_seed
        self.rng = _seeded_rng(build_seed, "residual_vault")
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(1, int(cap))
        self._emitted: List[bool] = []
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        if self._emitted:
            return 0
        parent: Dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parent[id(ch)] = node
        cands: List[Tuple[ast.Constant, int, bytes]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if is_generated(node):
                continue
            v = node.value
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, int):
                kind = 0
                plain = str(v).encode("ascii")
            elif isinstance(v, str):
                if len(v) == 0:
                    continue
                kind = 1
                try:
                    plain = v.encode("utf-8")
                except UnicodeEncodeError:
                    try:
                        plain = v.encode("utf-8", "surrogatepass")
                    except Exception:
                        continue
            else:
                continue
            p = parent.get(id(node))
            if isinstance(p, ast.JoinedStr):
                continue
            if isinstance(p, ast.Expr):
                gp = parent.get(id(p))
                if isinstance(gp, (ast.Module, ast.FunctionDef,
                                   ast.AsyncFunctionDef, ast.ClassDef,
                                   ast.Lambda)):
                    body = getattr(gp, "body", None)
                    if isinstance(body, list) and body and body[0] is p:
                        continue
            if self.rng.random() > self.coverage:
                continue
            cands.append((node, kind, plain))
            if len(cands) >= self.cap:
                break
        if not cands:
            return 0
        used = _used_identifiers(tree)
        vault_name = _gen_unique(
            self.naming, f"crypt::vault::{self.build_seed}", used)
        used.add(vault_name)
        getter_name = _gen_unique(
            self.naming, f"crypt::get::{self.build_seed}", used)
        salt = bytes(self.rng.randrange(256) for _ in range(8))
        shape_rng = _seeded_rng(self.build_seed, "crypt::saltshape")
        elts = []
        for i, b in enumerate(salt):
            kd = hashlib.sha256(
                f"{self.build_seed}::crypt::salt::{i}".encode()).digest()
            k1, k2 = kd[0], kd[1] | 1
            a = _cjk_lambda_arg(self.build_seed, "cryptsalt", i)
            elts.append(_hidden_char_expr(a, k1, k2, b, shape_rng))
        salt_expr = "bytes([" + ", ".join(elts) + "])"
        entries: Dict[int, Tuple[int, bytes]] = {}
        for idx, (_node, kind, plain) in enumerate(cands):
            kh = hashlib.sha256(salt + str(idx).encode()).digest()
            entries[idx] = (kind, bytes(
                (x + kh[j % 32] + idx) & 255 for j, x in enumerate(plain)))
        getter_src = (
            f"def {getter_name}(_i):\n"
            f"    _k, _b = {vault_name}[_i]\n"
            f"    _h = __import__('hashlib').sha256(({salt_expr} + str(_i).encode())).digest()\n"
            f"    _d = bytes((_x - _h[_j % 32] - _i) & 255 for _j, _x in enumerate(_b))\n"
            f"    return int(_d.decode()) if _k == 0 else _d.decode('utf-8', 'surrogatepass')\n")
        vault_src = f"{vault_name} = {entries!r}\n"
        try:
            vault_node = ast.parse(vault_src).body[0]
            getter_node = ast.parse(getter_src).body[0]
        except SyntaxError:
            return 0
        mark_generated(vault_node)
        mark_generated(getter_node)
        j = 0
        if (tree.body and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)):
            j = 1
        while j < len(tree.body) and isinstance(tree.body[j], ast.ImportFrom) \
                and tree.body[j].module == "__future__":
            j += 1
        tree.body[j:j] = [vault_node, getter_node]
        by_id = {id(node): idx for idx, (node, _k, _p) in enumerate(cands)}
        gname = getter_name
        class _R(_SkipGeneratedTransformer):
            def visit_Constant(inner_self, nd: ast.Constant):
                if id(nd) in by_id and not is_generated(nd):
                    call = ast.Call(
                        func=ast.Name(id=gname, ctx=ast.Load()),
                        args=[ast.Constant(value=by_id[id(nd)])],
                        keywords=[])
                    mark_generated(call)
                    return ast.copy_location(call, nd)
                return nd
        _R().visit(tree)
        ast.fix_missing_locations(tree)
        self._emitted.append(True)
        return len(cands)
def apply_module_maze(tree: ast.Module, naming: "RuneNaming",
                      build_seed: int, segments: int = 3) -> Tuple[int, List[str]]:
    body = tree.body
    j = 0
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        j = 1
    while j < len(body) and isinstance(body[j], ast.ImportFrom) \
            and body[j].module == "__future__":
        j += 1
    rest = body[j:]
    if len(rest) < 4:
        return 0, []
    if _is_entry_wrapped_tree(tree) is not None:
        return 0, []
    nseg = max(2, min(int(segments), 5, len(rest) // 2))
    if nseg < 2:
        return 0
    mrng = _seeded_rng(build_seed, "module_maze")
    used = _used_identifiers(tree)
    seg_var = _gen_unique(naming, f"miasma::seg::{build_seed}", used)
    used.add(seg_var)
    salt_var = _gen_unique(naming, f"miasma::salt::{build_seed}", used)
    used.add(salt_var)
    run_fn = _gen_unique(naming, f"miasma::run::{build_seed}", used)
    used.add(run_fn)
    st_var = _gen_unique(naming, f"miasma::st::{build_seed}", used)
    poly = _envelope_poly(build_seed)
    mid = poly["mask_id"]
    mcomp = poly.get("comp", "zlib")
    salt = bytes(mrng.randrange(256) for _ in range(8))
    shape_rng = _seeded_rng(build_seed, "miasma::saltshape")
    elts = []
    for i, b in enumerate(salt):
        kd = hashlib.sha256(
            f"{build_seed}::miasma::salt::{i}".encode()).digest()
        k1, k2 = kd[0], kd[1] | 1
        a = _cjk_lambda_arg(build_seed, "miasmasalt", i)
        elts.append(_hidden_char_expr(a, k1, k2, b, shape_rng))
    salt_expr = "bytes([" + ", ".join(elts) + "])"
    bounds = sorted(mrng.sample(range(1, len(rest)), nseg - 1))
    parts = [rest[a:b] for a, b in zip([0] + bounds, bounds + [len(rest)])]
    blobs: Dict[int, bytes] = {}
    for idx, part in enumerate(parts):
        try:
            src = ast.unparse(ast.Module(body=list(part), type_ignores=[]))
        except Exception:
            return 0
        enc = base64.b85encode(_compress_for_poly(src.encode("utf-8"), poly, 9))
        t_seg = hashlib.sha256(salt + str(idx).encode()).digest()[0]
        blobs[idx] = bytes(_poly_mask_enc(mid, b, i, len(enc), t_seg)
                           for i, b in enumerate(enc))
    order = list(range(nseg))
    mrng.shuffle(order)
    cases = []
    for i in order:
        nxt = i + 1 if i + 1 < nseg else -1
        call = ast.Expr(value=ast.Call(
            func=ast.Name(id=run_fn, ctx=ast.Load()),
            args=[ast.Constant(value=i)], keywords=[]))
        mark_generated(call)
        cases.append(ast.match_case(
            pattern=ast.MatchValue(value=ast.Constant(value=i)),
            body=[call, ast.Assign(
                targets=[ast.Name(id=st_var, ctx=ast.Store())],
                value=ast.Constant(value=nxt))]))
    junk = ast.Expr(value=ast.BinOp(
        left=ast.Constant(value=mrng.randint(10 ** 6, 10 ** 9)),
        op=ast.BitXor(),
        right=ast.Constant(value=mrng.randint(10 ** 6, 10 ** 9))))
    mark_generated(junk)
    cases.append(ast.match_case(
        pattern=ast.MatchAs(),
        body=[junk, ast.Assign(
            targets=[ast.Name(id=st_var, ctx=ast.Store())],
            value=ast.Constant(value=-1))]))
    unmask = _poly_mask_dec_src(mid, "_x", "_j", "len(_r)", "_t")
    run_src = (
        f"def {run_fn}(_i):\n"
        f"    _r = {seg_var}[_i]\n"
        f"    _t = __import__('hashlib').sha256({salt_var} + str(_i).encode()).digest()[0]\n"
        f"    _d = bytes({unmask} for _j, _x in enumerate(_r))\n"
        f"    exec(__import__('{mcomp}').decompress(__import__('base64').b85decode(_d)), globals())\n")
    try:
        seg_node = ast.parse(f"{seg_var} = {blobs!r}\n").body[0]
        salt_node = ast.parse(f"{salt_var} = {salt_expr}\n").body[0]
        run_node = ast.parse(run_src).body[0]
    except SyntaxError:
        return 0
    init = ast.Assign(targets=[ast.Name(id=st_var, ctx=ast.Store())],
                      value=ast.Constant(value=0))
    driver = ast.While(
        test=ast.Compare(left=ast.Name(id=st_var, ctx=ast.Load()),
                         ops=[ast.NotEq()],
                         comparators=[ast.Constant(value=-1)]),
        body=[ast.Match(subject=ast.Name(id=st_var, ctx=ast.Load()),
                        cases=cases)],
        orelse=[])
    for nd in (seg_node, salt_node, run_node, init, driver):
        mark_generated(nd)
        ast.fix_missing_locations(nd)
    snap_var = _gen_unique(naming, f"miasma::snap::{build_seed}", used)
    take_snap = ast.Assign(
        targets=[ast.Name(id=snap_var, ctx=ast.Store())],
        value=ast.Call(func=ast.Name(id="set", ctx=ast.Load()),
                       args=[ast.Call(
                           func=ast.Name(id="globals", ctx=ast.Load()),
                           args=[], keywords=[])],
                       keywords=[]))
    sweep = ast.parse(
        f"for {st_var} in [k for k in globals() if k not in {snap_var} "
        f"and k != {st_var!r} and k != {snap_var!r} "
        f"and not k.startswith('__')]:\n"
        f"    globals().pop({st_var}, None)\n"
        f"del {st_var}\n"
        f"del {snap_var}\n").body
    for nd in list(sweep) + [take_snap]:
        mark_generated(nd)
        ast.fix_missing_locations(nd)
    mark_generated(take_snap)
    tree.body = (body[:j] + [seg_node, salt_node, run_node, init, take_snap,
                             driver] + list(sweep))
    ast.fix_missing_locations(tree)
    return nseg, [seg_var, salt_var, run_fn]
class IfConjunctSplitPass(_SkipGeneratedTransformer):
    name = "if_conjunct_split"
    def __init__(self, build_seed: int, coverage: float = 0.55,
                 cap: int = 120, max_arms: int = 4):
        self.rng = _seeded_rng(build_seed, "if_conjunct_split")
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self.max_arms = max(2, int(max_arms))
        self._done = [0]
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        class _V(_SkipGeneratedTransformer):
            def visit_If(inner_self, node: ast.If):
                inner_self.generic_visit(node)
                if (outer._done[0] >= outer.cap
                        or not isinstance(node.test, ast.BoolOp)
                        or is_generated(node.test)):
                    return node
                values = node.test.values
                if len(values) < 2 or len(values) > outer.max_arms:
                    return node
                if outer.rng.random() > outer.coverage:
                    return node
                if isinstance(node.test.op, ast.And):
                    if node.orelse:
                        return node
                    inner = ast.If(test=values[-1], body=node.body, orelse=[])
                    for v in reversed(values[:-1]):
                        inner = ast.If(test=v, body=[inner], orelse=[])
                    ast.copy_location(inner, node)
                    ast.fix_missing_locations(inner)
                    outer._done[0] += 1
                    count[0] += 1
                    return inner
                head = ast.If(test=values[-1],
                              body=list(node.body), orelse=list(node.orelse))
                for v in reversed(values[:-1]):
                    head = ast.If(test=v, body=list(node.body), orelse=[head])
                ast.copy_location(head, node)
                ast.fix_missing_locations(head)
                outer._done[0] += 1
                count[0] += 1
                return head
        _V().visit(tree)
        return count[0]
class IfExpSplitPass(_SkipGeneratedTransformer):
    name = "ifexp_split"
    def __init__(self, build_seed: int, coverage: float = 0.6,
                 cap: int = 120):
        self.rng = _seeded_rng(build_seed, "ifexp_split")
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
    def _split(self, test, mk_then, mk_else, node):
        if self._done[0] >= self.cap or self.rng.random() > self.coverage:
            return node
        new = ast.If(test=test, body=[mk_then()], orelse=[mk_else()])
        mark_generated(new)
        ast.copy_location(new, node)
        ast.fix_missing_locations(new)
        self._done[0] += 1
        return new
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        before = self._done[0]
        super().visit(tree)
        return self._done[0] - before
    def visit_Assign(self, node: ast.Assign):
        self.generic_visit(node)
        if (len(node.targets) >= 1
                and all(isinstance(t, ast.Name) for t in node.targets)
                and isinstance(node.value, ast.IfExp)
                and not is_generated(node)):
            e = node.value
            tgts = list(node.targets)
            return self._split(
                e.test,
                lambda: ast.Assign(targets=copy.deepcopy(tgts), value=e.body),
                lambda: ast.Assign(targets=copy.deepcopy(tgts), value=e.orelse),
                node)
        return node
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.generic_visit(node)
        if (node.value is not None and node.simple
                and isinstance(node.value, ast.IfExp)
                and not is_generated(node)):
            e = node.value
            return self._split(
                e.test,
                lambda: ast.AnnAssign(
                    target=copy.deepcopy(node.target),
                    annotation=copy.deepcopy(node.annotation),
                    value=e.body, simple=node.simple),
                lambda: ast.AnnAssign(
                    target=copy.deepcopy(node.target),
                    annotation=copy.deepcopy(node.annotation),
                    value=e.orelse, simple=node.simple),
                node)
        return node
    def visit_Return(self, node: ast.Return):
        self.generic_visit(node)
        if (node.value is not None and isinstance(node.value, ast.IfExp)
                and not is_generated(node)):
            e = node.value
            return self._split(
                e.test,
                lambda: ast.Return(value=e.body),
                lambda: ast.Return(value=e.orelse),
                node)
        return node
    def visit_Expr(self, node: ast.Expr):
        self.generic_visit(node)
        if (isinstance(node.value, ast.IfExp)
                and not is_generated(node)):
            e = node.value
            return self._split(
                e.test,
                lambda: ast.Expr(value=e.body),
                lambda: ast.Expr(value=e.orelse),
                node)
        return node
class ZeroDivStatementWrapPass(_SkipGeneratedTransformer):
    name = "zero_div_wrap"
    _WRAPPABLE = (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr)
    def __init__(self, naming: "RuneNaming", cap: int = 0,
                 global_cap: Optional[int] = None):
        self.naming = naming
        self.cap = max(0, int(cap))
        self.global_cap = global_cap
        self._counter = [0]
        self._global_done = [0]
        self._helper_name = ""
    def _unsafe_body(self, body: List[ast.stmt]) -> bool:
        for nd in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(nd, (ast.Yield, ast.YieldFrom, ast.Await,
                               ast.Global, ast.Nonlocal, ast.Match)):
                return True
        return False
    def _ensure_helper(self, tree: ast.Module) -> None:
        if self._helper_name:
            return
        self._helper_name = self.naming.generate("zdw::helper")
        st = ast.parse(
            f"def {self._helper_name}(_):\n    return _\n").body[0]
        mark_generated(st)
        ast.fix_missing_locations(st)
        tree.body.insert(_module_insert_index(tree), st)
    def _wrap(self, stmt: ast.stmt) -> ast.Try:
        self._counter[0] += 1
        decoy = ast.Expr(value=ast.Call(
            func=ast.Name(id=self._helper_name, ctx=ast.Load()),
            args=[ast.BinOp(left=ast.Constant(value=1),
                            op=ast.FloorDiv(),
                            right=ast.Constant(value=0))],
            keywords=[]))
        trial = ast.Try(
            body=[decoy],
            handlers=[ast.ExceptHandler(
                type=ast.Name(id="ZeroDivisionError", ctx=ast.Load()),
                name=None,
                body=[stmt],
            )],
            orelse=[], finalbody=[],
        )
        mark_generated(decoy)
        mark_generated(trial.handlers[0].type)
        ast.fix_missing_locations(trial)
        return trial
    def _process_fn(self, node) -> List[ast.stmt]:
        body = node.body
        if not body or self.cap <= 0:
            return body
        if (self.global_cap is not None
                and self._global_done[0] >= self.global_cap):
            return body
        if self._unsafe_body(body):
            return body
        out: List[ast.stmt] = []
        done = 0
        for i, st in enumerate(body):
            if (i == 0 and isinstance(st, ast.Expr)
                    and isinstance(st.value, ast.Constant)
                    and isinstance(st.value.value, str)):
                out.append(st)
                continue
            if (isinstance(st, self._WRAPPABLE)
                    and not is_generated(st)
                    and done < self.cap
                    and (self.global_cap is None
                         or self._global_done[0] < self.global_cap)):
                out.append(self._wrap(st))
                done += 1
                self._global_done[0] += 1
            else:
                out.append(st)
        return out
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        self._ensure_helper(tree)
        count = [0]
        class _V(_SkipGeneratedTransformer):
            def visit_FunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                new_body = self._process_fn(node)
                if new_body is not node.body:
                    count[0] += 1
                    node.body = new_body
                return node
            def visit_AsyncFunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                return node
            def visit_ClassDef(inner_self, node):
                inner_self.generic_visit(node)
                body = node.body
                if (body and not self._unsafe_body(body)
                        and (self.global_cap is None
                             or self._global_done[0] < self.global_cap)):
                    out: List[ast.stmt] = []
                    for i, st in enumerate(body):
                        if (i == 0 and isinstance(st, ast.Expr)
                                and isinstance(st.value, ast.Constant)
                                and isinstance(st.value.value, str)):
                            out.append(st)
                            continue
                        if (isinstance(st, self._WRAPPABLE)
                                and not is_generated(st)
                                and self.cap > 0
                                and (self.global_cap is None
                                     or self._global_done[0] < self.global_cap)):
                            out.append(self._wrap(st))
                            self._global_done[0] += 1
                            count[0] += 1
                        else:
                            out.append(st)
                    node.body = out
                return node
        _V().visit(tree)
        return count[0]
class CallIndirectionPass(_SkipGeneratedTransformer):
    name = "call_indirection"
    def __init__(self, naming: "RuneNaming", build_seed: int,
                 unsafe_names: Optional[Set[str]] = None, max_funcs: int = 24):
        self.naming = naming
        self.build_seed = build_seed
        self.unsafe = unsafe_names or set()
        self.max_funcs = max_funcs
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        targets: Dict[str, str] = {}
        for stmt in tree.body:
            if (isinstance(stmt, ast.FunctionDef) and len(targets) < self.max_funcs
                    and not is_dunder(stmt.name) and stmt.name not in BUILTIN_NAMES
                    and stmt.name not in self.unsafe
                    and not stmt.name.startswith("_dzydkh")):
                targets[stmt.name] = self.naming.generate(f"ci::{stmt.name}::{self.build_seed}")
        if not targets:
            return 0
        count = [0]
        class _R(_SkipGeneratedTransformer):
            def visit_Call(inner_self, node: ast.Call):
                inner_self.generic_visit(node)
                if (isinstance(node.func, ast.Name)
                        and node.func.id in targets):
                    count[0] += 1
                    return ast.copy_location(
                        ast.Call(func=ast.Name(id=targets[node.func.id], ctx=ast.Load()),
                                 args=node.args, keywords=node.keywords), node)
                return node
        _R().visit(tree)
        by_name = {stmt.name: i for i, stmt in enumerate(tree.body)
                   if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))}
        insertions: List[Tuple[int, ast.stmt]] = []
        for orig, alias in targets.items():
            a = ast.parse(f"{alias} = {orig}").body[0]
            mark_generated(a)
            ast.fix_missing_locations(a)
            base = by_name.get(orig)
            if base is None:
                continue
            insertions.append((base + 1, a))
        for offset, (pos, node) in enumerate(sorted(insertions, key=lambda t: t[0])):
            tree.body.insert(pos + offset, node)
        ast.fix_missing_locations(tree)
        return count[0]
class ReturnSplitPass(_SkipGeneratedTransformer):
    name = "return_split"
    def __init__(self, naming: "RuneNaming", cap: int = 60):
        self.naming = naming
        self.cap = max(0, cap)
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        class _F(_SkipGeneratedTransformer):
            def _rewrite_body(inner_self, body):
                out = []
                changed = False
                for stmt in body:
                    if (isinstance(stmt, ast.Return) and stmt.value is not None
                            and not isinstance(stmt.value, (ast.Constant, ast.Name))
                            and count[0] < self.cap and not is_generated(stmt)):
                        tmp = self.naming.generate(f"rs::{count[0]}")
                        assign = ast.Assign(
                            targets=[ast.Name(id=tmp, ctx=ast.Store())],
                            value=stmt.value,
                            lineno=getattr(stmt, "lineno", 0),
                            col_offset=getattr(stmt, "col_offset", 0))
                        ast.copy_location(assign, stmt)
                        ast.fix_missing_locations(assign)
                        stmt.value = ast.Name(id=tmp, ctx=ast.Load())
                        ast.fix_missing_locations(stmt)
                        out.append(assign)
                        out.append(stmt)
                        count[0] += 1
                        changed = True
                    else:
                        out.append(stmt)
                return out, changed
            def visit_FunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                new_body, changed = inner_self._rewrite_body(node.body)
                if changed:
                    node.body = new_body
                return node
            def visit_AsyncFunctionDef(inner_self, node):
                inner_self.generic_visit(node)
                return node
        _F().visit(tree)
        return count[0]
class ImportIndirectionPass(TransformationPass):
    name = "import_indirection"
    SAFE_MODULES = {
        "sys", "os", "math", "time", "json", "re", "zlib", "base64",
        "hashlib", "hmac", "random", "struct", "io", "string", "textwrap",
        "datetime", "collections", "itertools", "functools", "types",
        "copy", "pathlib", "uuid", "shutil", "tempfile", "threading",
    }
    def __init__(self, naming: "RuneNaming", build_seed: int):
        self.naming = naming
        self.build_seed = build_seed
    def _encode_name(self, mod: str, idx: int) -> ast.expr:
        data = mod.encode("utf-8")
        elts = []
        for bi, b in enumerate(data):
            key = hashlib.sha256(
                f"{self.build_seed}::impind::{idx}::{bi}".encode()).digest()[0]
            param = self.naming.generate(f"ii::{idx}::{bi}::{self.build_seed}")
            lam = ast.Lambda(
                args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=param)],
                                   kwonlyargs=[], kw_defaults=[], defaults=[],
                                   vararg=None, kwarg=None),
                body=ast.BinOp(left=ast.Name(id=param, ctx=ast.Load()),
                               op=ast.BitXor(), right=ast.Constant(value=key)))
            elts.append(ast.Call(func=lam, args=[ast.Constant(value=b ^ key)], keywords=[]))
        bytes_call = ast.Call(func=ast.Name(id="bytes", ctx=ast.Load()),
                              args=[ast.List(elts=elts, ctx=ast.Load())], keywords=[])
        return ast.Call(func=ast.Attribute(value=bytes_call, attr="decode", ctx=ast.Load()),
                        args=[ast.Constant(value="utf-8")], keywords=[])
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        rebinds: Dict[str, Tuple[str, ast.expr]] = {}
        remove_idx = []
        for i, stmt in enumerate(tree.body):
            if not isinstance(stmt, ast.Import):
                continue
            if len(stmt.names) != 1:
                continue
            alias = stmt.names[0]
            root = alias.name.split(".")[0]
            if root not in self.SAFE_MODULES or "." in alias.name:
                continue
            bound = alias.asname or root
            gen = self.naming.generate(f"iib::{bound}::{self.build_seed}")
            rebinds[bound] = (gen, self._encode_name(alias.name, len(rebinds)))
            remove_idx.append(i)
        if not rebinds:
            return 0
        for i in reversed(remove_idx):
            del tree.body[i]
        for bound, (gen, enc) in rebinds.items():
            assign = ast.Assign(
                targets=[ast.Name(id=gen, ctx=ast.Store())],
                value=ast.Call(func=ast.Name(id="__import__", ctx=ast.Load()),
                               args=[enc], keywords=[]),
            )
            mark_generated(assign)
            ast.fix_missing_locations(assign)
            tree.body.insert(_module_insert_index(tree), assign)
        count = [0]
        class _R(_SkipGeneratedTransformer):
            def visit_Name(inner_self, node: ast.Name):
                if isinstance(node.ctx, ast.Load) and node.id in rebinds:
                    count[0] += 1
                    return ast.copy_location(
                        ast.Name(id=rebinds[node.id][0], ctx=ast.Load()), node)
                return node
        _R().visit(tree)
        return count[0]
class FromImportHidePass(TransformationPass):
    name = "from_import_hide"
    def __init__(self, naming: "RuneNaming", build_seed: int,
                 coverage: float = 0.8, cap: int = 120):
        self.naming = naming
        self.build_seed = build_seed
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
        self._site = [0]
    def _encode_name(self, mod: str) -> ast.expr:
        data = mod.encode("utf-8")
        idx = self._site[0]
        elts = []
        for bi, b in enumerate(data):
            key = hashlib.sha256(
                f"{self.build_seed}::veil::{idx}::{bi}".encode()).digest()[0]
            param = self.naming.generate(
                f"veil::{idx}::{bi}::{self.build_seed}")
            lam = ast.Lambda(
                args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=param)],
                                   kwonlyargs=[], kw_defaults=[], defaults=[],
                                   vararg=None, kwarg=None),
                body=ast.BinOp(left=ast.Name(id=param, ctx=ast.Load()),
                               op=ast.BitXor(), right=ast.Constant(value=key)))
            elts.append(ast.Call(func=lam, args=[ast.Constant(value=b ^ key)],
                                 keywords=[]))
        self._site[0] += 1
        bytes_call = ast.Call(func=ast.Name(id="bytes", ctx=ast.Load()),
                              args=[ast.List(elts=elts, ctx=ast.Load())],
                              keywords=[])
        return ast.Call(func=ast.Attribute(value=bytes_call, attr="decode",
                                           ctx=ast.Load()),
                        args=[ast.Constant(value="utf-8")], keywords=[])
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        class _V(_SkipGeneratedTransformer):
            def _one(inner_self, node: ast.ImportFrom):
                if (outer._done[0] >= outer.cap
                        or (node.level or 0) != 0
                        or node.module in (None, "__future__")
                        or not node.names
                        or any(a.name == "*" for a in node.names)
                        or outer._done[0] >= outer.cap
                        or rng.random() > outer.coverage):
                    return [node]
                mod = node.module
                assert isinstance(mod, str)
                tmp = outer.naming.generate(
                    f"veil::mod::{outer._done[0]}::{outer.build_seed}")
                fromlist = ast.List(
                    elts=[ast.Constant(value=a.name) for a in node.names],
                    ctx=ast.Load())
                imp = ast.Assign(
                    targets=[ast.Name(id=tmp, ctx=ast.Store())],
                    value=ast.Call(
                        func=ast.Name(id="__import__", ctx=ast.Load()),
                        args=[outer._encode_name(mod),
                              ast.Call(func=ast.Name(id="globals", ctx=ast.Load()),
                                       args=[], keywords=[]),
                              ast.Call(func=ast.Name(id="locals", ctx=ast.Load()),
                                       args=[], keywords=[]),
                              fromlist],
                        keywords=[]))
                stmts: List[ast.stmt] = [imp]
                for a in node.names:
                    bound = a.asname or a.name
                    stmts.append(ast.Assign(
                        targets=[ast.Name(id=bound, ctx=ast.Store())],
                        value=ast.Call(
                            func=ast.Name(id="getattr", ctx=ast.Load()),
                            args=[ast.Name(id=tmp, ctx=ast.Load()),
                                  ast.Constant(value=a.name)],
                            keywords=[])))
                for st in stmts:
                    mark_generated(st)
                ast.fix_missing_locations(imp)
                for st in stmts[1:]:
                    ast.fix_missing_locations(st)
                outer._done[0] += 1
                count[0] += 1
                return stmts
            def _body(inner_self, body: List[ast.stmt]) -> None:
                out: List[ast.stmt] = []
                for st in body:
                    if (isinstance(st, ast.ImportFrom)
                            and not is_generated(st)):
                        out.extend(inner_self._one(st))
                    else:
                        out.append(st)
                body[:] = out
            def visit_Module(inner_self, node: ast.Module):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                return node
            def visit_FunctionDef(inner_self, node: ast.FunctionDef):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                return node
            def visit_AsyncFunctionDef(inner_self, node: ast.AsyncFunctionDef):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                return node
            def visit_ClassDef(inner_self, node: ast.ClassDef):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                return node
            def visit_If(inner_self, node: ast.If):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                inner_self._body(node.orelse)
                return node
            def visit_Try(inner_self, node: ast.Try):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                for h in node.handlers:
                    inner_self._body(h.body)
                inner_self._body(node.orelse)
                inner_self._body(node.finalbody)
                return node
            def visit_While(inner_self, node: ast.While):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                inner_self._body(node.orelse)
                return node
            def visit_For(inner_self, node: ast.For):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                inner_self._body(node.orelse)
                return node
            def visit_With(inner_self, node: ast.With):
                inner_self.generic_visit(node)
                inner_self._body(node.body)
                return node
        _V().visit(tree)
        ast.fix_missing_locations(tree)
        return count[0]
class RequestsForgePass(TransformationPass):
    name = "requests_forge"
    VERBS = ("get", "post", "put", "delete", "patch", "head", "options")
    def __init__(self, naming: "RuneNaming", build_seed: int,
                 coverage: float = 0.85, cap: int = 64):
        self.naming = naming
        self.build_seed = build_seed
        self.coverage = min(1.0, max(0.05, coverage))
        self.cap = max(0, int(cap))
        self._done = [0]
        self._helper: List[str] = []
        self._k1: int = 0
    def _has_requests(self, tree: ast.Module) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "requests" or a.name.startswith("requests."):
                        return True
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "") == "requests" or (
                        node.module or "").startswith("requests."):
                    return True
        return False
    def _aliases(self, tree: ast.Module) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for node in ast.walk(tree):
            if is_generated(node):
                continue
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "requests":
                        out[a.asname or "requests"] = "mod"
            elif isinstance(node, ast.ImportFrom) and node.module == "requests":
                for a in node.names:
                    if a.name in self.VERBS and a.name != "*":
                        out[a.asname or a.name] = "func:" + a.name
        return out
    def _helper_def(self) -> ast.stmt:
        rng = _seeded_rng(self.build_seed, "rqforge")
        hn = self.naming.generate(f"rqforge::helper::{self.build_seed}")
        self._helper.append(hn)
        k1 = rng.randrange(1, 256)
        self._k1 = k1
        k2 = rng.randrange(1, 256)
        k3 = rng.randrange(1, 256)
        secret = hashlib.sha256(
            f"{self.build_seed}::rqforge::secret".encode()).digest()[:16]
        sec = bytes(b ^ k2 for b in secret)
        mod = bytes(b ^ k3 for b in b"requests")
        decoys = {}
        for _ in range(2):
            dk = "X-Dkh-" + "".join(
                rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
            dv = hashlib.sha256(
                f"{self.build_seed}::rqforge::{dk}".encode()).hexdigest()[:12]
            decoys[dk] = dv
        src = (
            f"def {hn}(_v, _e, *_a, **_k):\n"
            f"    _u = (bytes(_c ^ {k1} for _c in _e).decode('utf-8') if isinstance(_e, (bytes, bytearray)) else _e)\n"
            f"    _m = bytes(_c ^ {k3} for _c in {mod!r}).decode('utf-8')\n"
            f"    _s = bytes(_c ^ {k2} for _c in {sec!r})\n"
            f"    _h = dict(_k.pop('headers', None) or {{}})\n"
            f"    _h.update({decoys!r})\n"
            f"    _m2, _aa = (_a[0], _a[1:]) if _v == 'request' else (_v, _a)\n"
            f"    _h['X-Dkh-Sign'] = __import__('hmac').new(_s, (_m2 + _u).encode('utf-8'), __import__('hashlib').sha256).hexdigest()\n"
            f"    if _v == 'request':\n"
            f"        return getattr(__import__(_m), _v)(_m2, _u, *_aa, headers=_h, **_k)\n"
            f"    return getattr(__import__(_m), _v)(_u, *_a, headers=_h, **_k)\n")
        node = ast.parse(src).body[0]
        mark_generated(node)
        ast.fix_missing_locations(node)
        return node
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        if not self._has_requests(tree):
            return 0
        self._trng = rng
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) \
                    and node.id == "requests":
                return 0
            if isinstance(node, ast.arg) and node.arg == "requests":
                return 0
        aliases = self._aliases(tree)
        if not aliases:
            return 0
        count = [0]
        outer = self
        helper_name: List[str] = []
        helper_node: List[ast.stmt] = []
        def _ensure_helper() -> str:
            if not helper_name:
                node = outer._helper_def()
                helper_name.append(outer._helper[-1])
                helper_node.append(node)
            return helper_name[0]
        class _V(_SkipGeneratedTransformer):
            def visit_Call(inner_self, node: ast.Call):
                inner_self.generic_visit(node)
                if outer._done[0] >= outer.cap:
                    return node
                if any(kw.arg is None for kw in node.keywords):
                    return node
                if any(isinstance(a, ast.Starred) for a in node.args):
                    return node
                verb = None
                rest_pos: List[ast.expr] = []
                url_at = 0
                skip = {0}
                if isinstance(node.func, ast.Attribute) \
                        and isinstance(node.func.value, ast.Name) \
                        and aliases.get(node.func.value.id) == "mod" \
                        and node.func.attr in outer.VERBS \
                        and len(node.args) >= 1:
                    verb = node.func.attr
                    rest_pos = list(node.args)
                    url_at = 0
                    skip = {0}
                elif isinstance(node.func, ast.Attribute) \
                        and isinstance(node.func.value, ast.Name) \
                        and aliases.get(node.func.value.id) == "mod" \
                        and node.func.attr == "request" \
                        and len(node.args) >= 2 \
                        and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    verb = "request"
                    rest_pos = list(node.args)
                    url_at = 1
                    skip = {0, 1}
                elif isinstance(node.func, ast.Name):
                    bound = aliases.get(node.func.id)
                    if bound and bound.startswith("func:") and node.args:
                        verb = bound[5:]
                        rest_pos = list(node.args)
                        url_at = 0
                        skip = {0}
                    else:
                        return node
                else:
                    return node
                if not verb or outer._trng.random() > outer.coverage:
                    return node
                if url_at >= len(rest_pos):
                    return node
                hn = _ensure_helper()
                url_node = rest_pos[url_at]
                if isinstance(url_node, ast.Constant) \
                        and isinstance(url_node.value, str):
                    masked = bytes(
                        b ^ outer._k1
                        for b in url_node.value.encode("utf-8"))
                    url_arg: ast.expr = ast.copy_location(
                        ast.Constant(value=masked), url_node)
                else:
                    url_arg = url_node
                is_req_form = (isinstance(node.func, ast.Attribute)
                               and getattr(node.func, "attr", None) == "request")
                new = ast.Call(
                    func=ast.Name(id=hn, ctx=ast.Load()),
                    args=[ast.Constant(value=verb), url_arg]
                    + ([rest_pos[0]] if is_req_form else [])
                    + [a for i, a in enumerate(rest_pos) if i not in skip],
                    keywords=list(node.keywords))
                mark_generated(new)
                ast.copy_location(new, node)
                ast.fix_missing_locations(new)
                outer._done[0] += 1
                count[0] += 1
                return new
        _V().visit(tree)
        if helper_node:
            tree.body.insert(_module_insert_index(tree), helper_node[0])
        return count[0]
class StringDedupPass(_SkipGeneratedTransformer):
    name = "string_dedup"
    def __init__(self, naming: "RuneNaming"):
        self.naming = naming
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        seen: Dict[str, int] = {}
        canon: Dict[str, str] = {}
        class _Counter(_SkipGeneratedTransformer):
            def visit_Constant(inner_self, node: ast.Constant):
                if isinstance(node.value, str) and len(node.value) >= 4:
                    seen[node.value] = seen.get(node.value, 0) + 1
                return node
            def visit_JoinedStr(inner_self, node: ast.JoinedStr):
                node.values = [inner_self.visit(v)
                               if isinstance(v, ast.FormattedValue) else v
                               for v in node.values]
                return node
        _Counter().visit(tree)
        for value, n in seen.items():
            if n > 1:
                canon[value] = self.naming.generate(f"sded::{hashlib.sha256(value.encode()).hexdigest()[:8]}")
        if not canon:
            return 0
        replaced = [0]
        class _Rewrite(_SkipGeneratedTransformer):
            def visit_Constant(inner_self, node: ast.Constant):
                if isinstance(node.value, str) and node.value in canon:
                    if replaced[0] == 0:
                        replaced[0] += 1
                        return node
                    replaced[0] += 1
                    return ast.copy_location(
                        ast.Name(id=canon[node.value], ctx=ast.Load()), node)
                return node
            def visit_JoinedStr(inner_self, node: ast.JoinedStr):
                node.values = [inner_self.visit(v)
                               if isinstance(v, ast.FormattedValue) else v
                               for v in node.values]
                return node
        _Rewrite().visit(tree)
        assigns = []
        for value, name in canon.items():
            a = ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())],
                           value=ast.Constant(value=value))
            mark_generated(a)
            ast.fix_missing_locations(a)
            assigns.append(a)
        insert_at = _module_insert_index(tree)
        tree.body[insert_at:insert_at] = assigns
        ast.fix_missing_locations(tree)
        return replaced[0]
def _fast_ast_clone(node):
    if isinstance(node, ast.AST):
        new_node = node.__class__()
        for field_name in node._fields:
            setattr(new_node, field_name,
                    _fast_ast_clone(getattr(node, field_name, None)))
        for attr in ('lineno', 'col_offset', 'end_lineno', 'end_col_offset'):
            if attr in node.__dict__:
                setattr(new_node, attr, node.__dict__[attr])
        return new_node
    elif isinstance(node, list):
        return [_fast_ast_clone(x) for x in node]
    else:
        return node
_FIXUP_LIST_FIELDS = frozenset((
    "ifs", "generators", "values", "elts", "keys", "args", "keywords",
    "bases", "decorator_list", "body", "orelse", "finalbody",
    "handlers", "cases", "names", "defaults", "kw_defaults",
    "posonlyargs", "kwonlyargs", "ops", "comparators", "type_ignores",
))
def _fixup_ast_fields(node) -> None:
    try:
        for n in ast.walk(node):
            try:
                fields = getattr(type(n), "_fields", None)
                if not fields:
                    continue
                for fname in fields:
                    try:
                        getattr(n, fname)
                    except AttributeError:
                        try:
                            if fname == "is_async":
                                setattr(n, fname, 0)
                            elif fname in _FIXUP_LIST_FIELDS:
                                setattr(n, fname, [])
                            else:
                                setattr(n, fname, None)
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass
class ASTPassManager:
    def __init__(self, passes: List[TransformationPass], depth: int,
                 build_seed: int, max_nodes: int,
                 size_limit_bytes: Optional[int] = None):
        self.passes = passes
        self.depth = max(1, min(10, depth))
        self.build_seed = build_seed
        self.max_nodes = max_nodes
        self.size_limit_bytes = size_limit_bytes
        self.rounds_executed = 0
        self.rounds_reverted = 0
        self.round_sizes: List[int] = []
    def run(self, tree: ast.Module) -> Tuple[Dict[str, int], List[str]]:
        totals: Dict[str, int] = {p.name: 0 for p in self.passes}
        skipped: List[str] = []
        total_transformed = 0
        for round_idx in range(self.depth):
            if total_transformed >= self.max_nodes:
                skipped.append(
                    f"AST pass rounds stopped early at round {round_idx + 1}/{self.depth}: "
                    f"max_expansion_nodes ({self.max_nodes}) reached."
                )
                break
            round_snapshot = _fast_ast_clone(tree.body)
            round_counts: Dict[str, int] = {}
            round_failed = False
            for p in self.passes:
                rng = _seeded_rng(self.build_seed, f"astpass::{p.name}::round{round_idx}")
                try:
                    n = p.transform(tree, rng)
                    round_counts[p.name] = round_counts.get(p.name, 0) + n
                except Exception as e:
                    round_failed = True
                    skipped.append(f"[SKIPPED] AST pass '{p.name}' round {round_idx + 1}: "
                                    f"raised {e!r}; entire round reverted.")
                    break
            if not round_failed:
                try:
                    ast.fix_missing_locations(tree)
                    compile(tree, "<astpass_check>", "exec")
                except Exception as e:
                    round_failed = True
                    skipped.append(f"[SKIPPED] round {round_idx + 1}: post-round validation "
                                    f"failed ({e!r}); entire round reverted.")
            if not round_failed and self.size_limit_bytes is not None:
                try:
                    _fixup_ast_fields(tree)
                    projected = len(ast.unparse(tree).encode("utf-8"))
                    self.round_sizes.append(projected)
                    if projected > self.size_limit_bytes:
                        round_failed = True
                        skipped.append(
                            f"[SIZE_GUARD] round {round_idx + 1}: projected body "
                            f"{projected} B exceeds limit {self.size_limit_bytes} B; "
                            f"round reverted.")
                except Exception as e:
                    skipped.append(f"[SIZE_GUARD] measurement failed ({e!r}); kept round.")
            if round_failed:
                tree.body[:] = round_snapshot
                self.rounds_reverted += 1
                continue
            self.rounds_executed += 1
            for k, v in round_counts.items():
                totals[k] += v
                total_transformed += v
            if total_transformed >= self.max_nodes:
                break
        return totals, skipped
def run_structural_expansion(tree: ast.Module, config: DragonConfig,
                              naming: "RuneNaming",
                              unsafe_names: Optional[Set[str]] = None,
                              node_budget: Optional[int] = None,
                              rounds: Optional[int] = None,
                              med_cap: Optional[int] = None,
                              junk_cases: Optional[int] = None,
                              global_med_cap: Optional[int] = None,
                              force_extra_passes: bool = False,
                              size_limit_bytes: Optional[int] = None,
                              coverage: float = 0.55,
                              dead_path_cap: int = 0,
                              return_split_cap: int = 0,
                              ) -> Tuple[Dict[str, int], List[str], Dict[str, int]]:
    build_seed = config.seed if config.seed is not None else 0
    lvl = config.expansion_level
    gate = lvl in ("high", "extreme") or force_extra_passes
    has_functions = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        for n in ast.walk(tree))
    has_async_gen = any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await))
                        for n in ast.walk(tree)
                        ) and any(isinstance(n, ast.AsyncFunctionDef)
                                  for n in ast.walk(tree))
    plan = PipelinePlan({
        "scope_analysis": unsafe_names is not None,
        "functions_present_or_wrappable": bool(has_functions),
        "async_generators_present": bool(has_async_gen),
    })
    requested: List[Tuple[str, Any]] = [
        ("arithmetic_decomposition", ArithmeticDecompositionPass(coverage=coverage)),
        ("harmless_wrapper", HarmlessWrapperPass(
            coverage=max(0.3, coverage * 0.6), build_seed=build_seed)),
        ("decoy_statement", DecoyStatementPass(naming, lvl)),
    ]
    if config.enable_opaque_predicates and gate:
        requested.append(("opaque_predicate", OpaquePredicatePass(lvl, build_seed)))
    if getattr(config, "enable_handler_embed", True) and gate:
        requested.append(("handler_embed", HandlerEmbedPass(
            naming, build_seed,
            coverage=min(0.7, 0.25 + coverage * 0.5))))
    if getattr(config, "enable_decompiler_traps", True) and gate:
        requested.append(("decompiler_traps", DecompilerTrapsPass(
            naming, build_seed,
            coverage=min(0.7, 0.25 + coverage * 0.5))))
    if getattr(config, "enable_chain_links", True) and gate:
        _chain_rng = _seeded_rng(build_seed, "chainlinks::depth")
        requested.append(("chain_link", ChainLinkPass(
            naming, build_seed,
            coverage=min(0.8, 0.30 + coverage * 0.5),
            max_links=_chain_rng.randint(4, 8))))
    if config.enable_boolean_algebra and lvl != "minimal":
        requested.append(("boolean_algebra", BooleanAlgebraPass(
            apply_probability=min(0.9, 0.35 + coverage * 0.5))))
    if getattr(config, "enable_lambda_thunk", False) or config.more_obfuscation:
        requested.append(("lambda_thunk", LambdaThunkPass(
            naming, build_seed,
            coverage=min(0.6, 0.25 + coverage * 0.5))))
    if config.enable_control_flow and gate:
        requested.append(("tautology_guard", TautologyGuardPass(
            build_seed + 3, coverage=min(0.8, 0.35 + coverage * 0.6))))
        requested.append(("condition_swap", ConditionSwapPass(
            build_seed + 4, coverage=min(0.7, 0.30 + coverage * 0.5))))
        requested.append(("for_to_while", ForToWhilePass(
            build_seed + 7, coverage=min(0.7, 0.30 + coverage * 0.5))))
        requested.append(("if_conjunct_split", IfConjunctSplitPass(
            build_seed + 5, coverage=min(0.75, 0.35 + coverage * 0.5))))
        requested.append(("entry_guard", EntryGuardPass(
            build_seed + 6, coverage=min(0.9, 0.55 + coverage * 0.4))))
        requested.append(("zero_div_wrap", ZeroDivStatementWrapPass(
            naming, cap=max(2, med_cap), global_cap=global_med_cap)))
    if config.enable_memory_error_dispatch and gate:
        requested.append(("memory_error_dispatcher", MemoryErrorDispatcherPass(
            naming, lvl, cap_override=med_cap,
            junk_override=junk_cases, global_cap=global_med_cap)))
    if (config.enable_builtins_indirection or (config.more_obfuscation and gate)) and gate:
        requested.append(("builtins_indirection",
                          BuiltinsIndirectionPass(naming, build_seed)))
    if return_split_cap > 0:
        requested.append(("return_split", ReturnSplitPass(naming, cap=return_split_cap)))
    if dead_path_cap > 0:
        requested.append(("dead_path", DeadPathPass(naming, build_seed, cap=dead_path_cap)))
    if getattr(config, "enable_match_flatten", True) and gate:
        requested.append(("match_flatten", MatchFlattenPass(
            naming, build_seed,
            coverage=min(0.7, 0.25 + coverage * 0.5))))
    if getattr(config, "enable_from_import_hide", True) and gate:
        requested.append(("from_import_hide", FromImportHidePass(
            naming, build_seed,
            coverage=min(0.85, 0.4 + coverage * 0.5))))
    requested.append(("ifexp_split", IfExpSplitPass(
        build_seed,
        coverage=min(0.8, 0.35 + coverage * 0.5))))
    if getattr(config, "enable_residual_vault", True) and gate:
        requested.append(("residual_vault", ResidualVaultPass(
            naming, build_seed,
            coverage=min(0.85, 0.45 + coverage * 0.5))))
    if getattr(config, "enable_dead_bloat", True) and gate:
        _db_ratio = float(getattr(config, "dead_bloat_ratio", 0.15) or 0.0)
        if _db_ratio > 0:
            requested.append(("dead_bloat", DeadBloatPass(
                naming, build_seed, ratio=min(1.0, _db_ratio), cap=240)))
    if (config.enable_globals_storage and lvl == "extreme"):
        _unsafe = unsafe_names if unsafe_names is not None else set()
        requested.append(("globals_storage",
                          GlobalsStoragePass(naming, build_seed, _unsafe)))
    passes, plan_notes = plan.resolve(requested)
    effective_budget = node_budget if node_budget is not None else config._effective_max_nodes()
    effective_rounds = rounds if rounds is not None else config.ast_depth
    manager = ASTPassManager(passes, effective_rounds, build_seed, effective_budget,
                              size_limit_bytes=size_limit_bytes)
    totals, skipped = manager.run(tree)
    rounds_info = {
        "configured": manager.depth,
        "executed": manager.rounds_executed,
        "reverted": manager.rounds_reverted,
        "round_sizes": list(manager.round_sizes),
        "plan": [getattr(p, "name", "?") for p in passes],
        "plan_notes": list(plan_notes),
        "stage_order": list(_STAGE_ORDER),
    }
    return totals, skipped, rounds_info
RUNTIME_HELPER_NAME = "_ember_decode"
BYTES_HELPER_NAME = "_ember_decode_bytes"
LOOKUP_TABLE_NAME = "_ember_table"
LOOKUP_HELPER_NAME = "_ember_lookup"
class EmberNames:
    def __init__(self, build_seed: Optional[int] = None):
        if build_seed is None:
            self.lookup_helper = LOOKUP_HELPER_NAME
            self.bytes_helper = BYTES_HELPER_NAME
            self.table = LOOKUP_TABLE_NAME
        else:
            d = hashlib.sha256(f"ember::{build_seed}".encode()).hexdigest()[:10]
            self.lookup_helper = f"_el{d}"
            self.bytes_helper = f"_eb{d}"
            self.table = f"_et{d}"
def _legacy_ember_names() -> EmberNames:
    return EmberNames(None)
class FragmentPlanner:
    def __init__(self, expansion_level: str, build_seed: int):
        self.expansion_level = expansion_level
        self.build_seed = build_seed
        self._counter = 0
    def fragment_count_for(self, byte_length: int) -> int:
        if byte_length <= 4:
            return 1
        level_caps = {"minimal": 1, "light": 2, "high": 3, "extreme": 4}
        cap = level_caps.get(self.expansion_level, 2)
        if cap <= 1:
            return 1
        target = min(cap, max(1, byte_length // 6))
        return max(1, target)
    def spec_for(self, string_index: int, fragment_index: int) -> Tuple[int, bool, bool]:
        d = hashlib.sha256(
            f"{self.build_seed}::ember::{string_index}::{fragment_index}".encode()
        ).digest()
        return d[0], bool(d[1] & 1), bool(d[2] & 1)
def _split_bytes(data: bytes, n: int) -> List[bytes]:
    if n <= 1 or len(data) == 0:
        return [data]
    n = min(n, max(1, len(data)))
    size = len(data) // n
    fragments = []
    pos = 0
    for i in range(n - 1):
        take = size if size > 0 else 1
        fragments.append(data[pos:pos + take])
        pos += take
    fragments.append(data[pos:])
    return [f for f in fragments if len(f) > 0] or [data]
def _ember_encode_fragment(frag: bytes, key: int, reverse: bool,
                           add_mode: bool) -> bytes:
    if add_mode:
        enc = bytes((b + key) & 0xFF for b in frag)
    else:
        enc = bytes(b ^ key for b in frag)
    return enc[::-1] if reverse else enc
class _DocstringMarker(ast.NodeVisitor):
    def __init__(self):
        self.docstring_node_ids = set()
    def _check_body(self, body: List[ast.stmt]):
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            self.docstring_node_ids.add(id(body[0].value))
    def visit_Match(self, node: ast.Match):
        for handler in node.cases:
            for sub in ast.walk(handler.pattern):
                if isinstance(sub, ast.Constant):
                    self.docstring_node_ids.add(id(sub))
        self.generic_visit(node)
    def visit_Module(self, node: ast.Module):
        self._check_body(node.body)
        self.generic_visit(node)
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._check_body(node.body)
        self.generic_visit(node)
    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_ClassDef(self, node: ast.ClassDef):
        self._check_body(node.body)
        self.generic_visit(node)
class _StringProtector(ast.NodeTransformer):
    def __init__(self, docstring_ids: set,
                 fragment_planner: FragmentPlanner, lookup_entries: List[Tuple[int, bytes]],
                 bytes_key: int = 0x5A, names: Optional[EmberNames] = None):
        self.docstring_ids = docstring_ids
        self.fragment_planner = fragment_planner
        self.lookup_entries = lookup_entries
        self.bytes_key = bytes_key & 0xFF
        self.names = names if names is not None else _legacy_ember_names()
        self.protected_count = 0
        self.skipped_count = 0
        self.fragments_generated = 0
        self._string_index = 0
        self._in_annotation = False
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            return self._protect_str(node)
        if isinstance(node.value, (bytes, bytearray)):
            return self._protect_bytes(node)
        return node
    def _protect_str(self, node: ast.Constant):
        if id(node) in self.docstring_ids:
            self.skipped_count += 1
            return node
        if self._in_annotation:
            self.skipped_count += 1
            return node
        if node.value == "":
            self.skipped_count += 1
            return node
        data = node.value.encode("utf-8")
        n_fragments = self.fragment_planner.fragment_count_for(len(data))
        fragments = _split_bytes(data, n_fragments)
        indices: List[int] = []
        for frag_idx, frag in enumerate(fragments):
            key, rev_flag, add_mode = self.fragment_planner.spec_for(
                self._string_index, frag_idx)
            encoded = _ember_encode_fragment(frag, key, rev_flag, add_mode)
            packed = key | (0x100 if rev_flag else 0) | (0x200 if add_mode else 0)
            table_index = len(self.lookup_entries)
            self.lookup_entries.append((packed, encoded))
            indices.append(table_index)
            self.fragments_generated += 1
        self._string_index += 1
        call = ast.Call(
            func=ast.Name(id=self.names.lookup_helper, ctx=ast.Load()),
            args=[ast.Tuple(elts=[ast.Constant(value=i) for i in indices], ctx=ast.Load())],
            keywords=[],
        )
        self.protected_count += 1
        return ast.copy_location(call, node)
    def _protect_bytes(self, node: ast.Constant):
        if self._in_annotation or len(node.value) == 0:
            self.skipped_count += 1
            return node
        encoded = _ember_encode_fragment(bytes(node.value)
                                          if isinstance(node.value, bytearray)
                                          else node.value,
                                          self.bytes_key, False, False)
        call = ast.Call(
            func=ast.Name(id=self.names.bytes_helper, ctx=ast.Load()),
            args=[ast.Constant(value=encoded)],
            keywords=[],
        )
        self.protected_count += 1
        return ast.copy_location(call, node)
    def visit_JoinedStr(self, node: ast.JoinedStr):
        self.skipped_count += 1
        return node
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._in_annotation = True
        node.annotation = self.visit(node.annotation)
        self._in_annotation = False
        if node.value:
            node.value = self.visit(node.value)
        return node
    def visit_arg(self, node: ast.arg):
        if node.annotation:
            self._in_annotation = True
            node.annotation = self.visit(node.annotation)
            self._in_annotation = False
        return node
class _DocstringStripper(ast.NodeTransformer):
    def _strip(self, body: List[ast.stmt]) -> List[ast.stmt]:
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        return body if body else [ast.Pass()]
    def visit_Module(self, node: ast.Module):
        self.generic_visit(node)
        node.body = self._strip(node.body)
        return node
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.generic_visit(node)
        node.body = self._strip(node.body)
        return node
    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node)
        node.body = self._strip(node.body)
        return node
def strip_docstrings(tree: ast.AST) -> int:
    counter = [0]
    def _count(n):
        if (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)):
            counter[0] += 1
    for node in ast.walk(tree):
        if hasattr(node, "body") and isinstance(getattr(node, "body"), list) and node.body:
            _count(node.body[0])
    _DocstringStripper().visit(tree)
    ast.fix_missing_locations(tree)
    return counter[0]
def count_protectable_literals(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                continue
            if isinstance(v, (str, bytes, int, float)):
                n += 1
    return n
def _emit_ember_table_helpers(tree: ast.Module,
                               lookup_entries: List[Tuple[int, bytes]],
                               build_seed: int, bytes_key: int,
                               names: Optional[EmberNames] = None) -> None:
    nm = names if names is not None else _legacy_ember_names()
    _ld = hashlib.sha256(f"{build_seed}::ember_locals".encode()).hexdigest()
    _indices_n, _parts_n = f"_{_ld[0:8]}", f"_{_ld[8:16]}"
    _i_n, _k_n = f"_{_ld[16:24]}", f"_{_ld[24:32]}"
    _frag_n, _f_n = f"_{_ld[32:40]}", f"_{_ld[40:48]}"
    _data_n, _key_n = f"_{_ld[48:56]}", f"_{_ld[56:64]}"
    n = len(lookup_entries)
    order = list(range(n))
    if n > 1:
        rng = _seeded_rng(build_seed, "ember_table_order")
        rng.shuffle(order)
    remap = {old: new for new, old in enumerate(order)}
    shuffled_entries = [lookup_entries[old] for old in order]
    class _RemapIndices(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id == nm.lookup_helper:
                if node.args and isinstance(node.args[0], ast.Tuple):
                    for elt in node.args[0].elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                            elt.value = remap[elt.value]
            return node
    _RemapIndices().visit(tree)
    keys_repr = ", ".join(str(k) for k, _b in shuffled_entries)
    frags_repr = ", ".join(f"{b!r}" for _k, b in shuffled_entries)
    table_source = (f"{nm.table}_k = ({keys_repr}{',' if n == 1 else ''})\n"
                    f"{nm.table}_b = ({frags_repr}{',' if n == 1 else ''})\n")
    lookup_helper_source = (
        f"def {nm.lookup_helper}({_indices_n}):\n"
        f"    {_parts_n} = []\n"
        f"    for {_i_n} in {_indices_n}:\n"
        f"        {_k_n} = {nm.table}_k[{_i_n}]; {_frag_n} = {nm.table}_b[{_i_n}]\n"
        f"        if {_k_n} & 512:\n"
        f"            {_f_n} = bytes((_b - ({_k_n} & 255)) & 255 for _b in {_frag_n})\n"
        f"        else:\n"
        f"            {_f_n} = bytes(_b ^ ({_k_n} & 255) for _b in {_frag_n})\n"
        f"        if {_k_n} & 256:\n"
        f"            {_f_n} = {_f_n}[::-1]\n"
        f"        {_parts_n}.append({_f_n})\n"
        f"    return b''.join({_parts_n}).decode('utf-8')\n"
    )
    bytes_helper_source = (
        f"def {nm.bytes_helper}({_data_n}, {_key_n}={bytes_key}):\n"
        f"    return bytes(b ^ {_key_n} for b in {_data_n})\n"
    )
    helper_ast = ast.parse(table_source + lookup_helper_source + bytes_helper_source).body
    for st in helper_ast:
        mark_generated(st)
    insert_at = _module_insert_index(tree)
    tree.body[insert_at:insert_at] = helper_ast
    ast.fix_missing_locations(tree)
def _module_insert_index(tree: ast.Module) -> int:
    idx = 0
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            idx += 1
        else:
            break
    return idx
def protect(tree: ast.Module, config: DragonConfig) -> Tuple[int, int]:
    build_seed = config.seed if config.seed is not None else 0
    fragment_planner = FragmentPlanner(config.expansion_level, build_seed)
    bytes_key = hashlib.sha256(f"{build_seed}::ember_bytes_key".encode()).digest()[0]
    lookup_entries: List[Tuple[int, bytes]] = []
    ember_names = EmberNames(build_seed)
    marker = _DocstringMarker()
    marker.visit(tree)
    protector = _StringProtector(marker.docstring_node_ids,
                                  fragment_planner, lookup_entries,
                                  bytes_key=bytes_key, names=ember_names)
    protector.visit(tree)
    if protector.protected_count == 0:
        return 0, protector.skipped_count
    _emit_ember_table_helpers(tree, lookup_entries, build_seed, bytes_key,
                               names=ember_names)
    return protector.protected_count, protector.skipped_count
class _SerpentStringProtector(ast.NodeTransformer):
    def __init__(self, naming: RuneNaming, docstring_ids: Set[int],
                 build_seed: int):
        self.naming = naming
        self.docstring_ids = docstring_ids
        self.build_seed = build_seed
        self.protected_count = 0
        self.skipped_count = 0
        self._str_idx = 0
        self._in_annotation = False
    def _xor_keys(self, str_idx: int, byte_idx: int, purpose: str = "serpent") -> Tuple[int, int]:
        digest = hashlib.sha256(
            f"{self.build_seed}::{purpose}::{str_idx}::{byte_idx}".encode()
        ).digest()
        return digest[0], digest[1] | 1
    def _encode_data(self, data: bytes, str_idx: int,
                     purpose: str = "serpent") -> List[ast.expr]:
        calls: List[ast.expr] = []
        for bi, b in enumerate(data):
            k1, k2 = self._xor_keys(str_idx, bi, purpose)
            enc = b ^ k1 ^ k2
            param = self.naming.generate(
                f"sc::{purpose}::{str_idx}::{bi}::{self.build_seed}"
            )
            calls.append(self._split_call(param, k1, k2, enc))
        return calls
    def _split_call(self, param: str, k1: int, k2: int, enc_byte: int) -> ast.expr:
        lam = ast.Lambda(
            args=ast.arguments(
                posonlyargs=[], args=[ast.arg(arg=param)],
                kwonlyargs=[], kw_defaults=[], defaults=[],
                vararg=None, kwarg=None,
            ),
            body=ast.BinOp(
                left=ast.Name(id=param, ctx=ast.Load()),
                op=ast.BitXor(),
                right=ast.BinOp(
                    left=ast.Constant(value=k1),
                    op=ast.BitXor(),
                    right=ast.Constant(value=k2),
                ),
            ),
        )
        return ast.Call(func=lam, args=[ast.Constant(value=enc_byte)], keywords=[])
    def _wrap_str(self, data: bytes, str_idx: int) -> ast.expr:
        elts = self._encode_data(data, str_idx, "serpent")
        bytes_call = ast.Call(
            func=ast.Name(id="bytes", ctx=ast.Load()),
            args=[ast.List(elts=elts, ctx=ast.Load())],
            keywords=[],
        )
        return ast.Call(
            func=ast.Attribute(value=bytes_call, attr="decode", ctx=ast.Load()),
            args=[ast.Constant(value="utf-8")],
            keywords=[],
        )
    def _wrap_bytes(self, data: bytes, str_idx: int) -> ast.expr:
        elts = self._encode_data(data, str_idx, "serpent_bytes")
        return ast.Call(
            func=ast.Name(id="bytes", ctx=ast.Load()),
            args=[ast.List(elts=elts, ctx=ast.Load())],
            keywords=[],
        )
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            return self._protect_str(node)
        if isinstance(node.value, (bytes, bytearray)):
            return self._protect_bytes(node)
        return node
    def _protect_str(self, node: ast.Constant) -> ast.AST:
        if id(node) in self.docstring_ids:
            self.skipped_count += 1
            return node
        if self._in_annotation:
            self.skipped_count += 1
            return node
        if not node.value:
            self.skipped_count += 1
            return node
        idx = self._str_idx
        self._str_idx += 1
        try:
            data = node.value.encode("utf-8")
        except (UnicodeEncodeError, AttributeError):
            self.skipped_count += 1
            return node
        result = self._wrap_str(data, idx)
        self.protected_count += 1
        return ast.copy_location(result, node)
    def _protect_bytes(self, node: ast.Constant) -> ast.AST:
        if self._in_annotation:
            self.skipped_count += 1
            return node
        data = bytes(node.value) if isinstance(node.value, bytearray) else node.value
        if not data:
            self.skipped_count += 1
            return node
        idx = self._str_idx
        self._str_idx += 1
        result = self._wrap_bytes(data, idx)
        self.protected_count += 1
        return ast.copy_location(result, node)
    def visit_JoinedStr(self, node: ast.JoinedStr):
        self.skipped_count += 1
        return node
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._in_annotation = True
        node.annotation = self.visit(node.annotation)
        self._in_annotation = False
        if node.value:
            node.value = self.visit(node.value)
        return node
    def visit_arg(self, node: ast.arg):
        if node.annotation:
            self._in_annotation = True
            node.annotation = self.visit(node.annotation)
            self._in_annotation = False
        return node
def protect_serpent(tree: ast.Module, config: DragonConfig,
                    naming: RuneNaming) -> Tuple[int, int]:
    build_seed = config.seed if config.seed is not None else 0
    marker = _DocstringMarker()
    marker.visit(tree)
    protector = _SerpentStringProtector(naming, marker.docstring_node_ids, build_seed)
    protector.visit(tree)
    ast.fix_missing_locations(tree)
    return protector.protected_count, protector.skipped_count
class _ChrArithmeticEncoder:
    def __init__(self, build_seed: int):
        self._rng = _seeded_rng(build_seed, "chr_arith")
    def encode(self, s: str) -> ast.expr:
        join_args: List[ast.expr] = []
        for ch in s:
            code_point = ord(ch)
            part_a = self._rng.randint(1, max(1, code_point - 1)) if code_point > 1 else 0
            part_b = code_point - part_a
            lam = ast.Lambda(
                args=ast.arguments(
                    posonlyargs=[], args=[], kwonlyargs=[],
                    kw_defaults=[], defaults=[], vararg=None, kwarg=None,
                ),
                body=ast.BinOp(
                    left=ast.Constant(value=part_a),
                    op=ast.Add(),
                    right=ast.Constant(value=part_b),
                ),
            )
            chr_call = ast.Call(
                func=ast.Name(id="chr", ctx=ast.Load()),
                args=[ast.Call(func=lam, args=[], keywords=[])],
                keywords=[],
            )
            join_args.append(chr_call)
        return ast.Call(
            func=ast.Attribute(
                value=ast.Constant(value=""),
                attr="join",
                ctx=ast.Load(),
            ),
            args=[ast.List(elts=join_args, ctx=ast.Load())],
            keywords=[],
        )
class _ChrArithStringProtector(ast.NodeTransformer):
    def __init__(self, encoder: _ChrArithmeticEncoder,
                 docstring_ids: Set[int]):
        self.encoder = encoder
        self.docstring_ids = docstring_ids
        self.protected = 0
        self.skipped = 0
        self._in_annotation = False
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            if id(node) in self.docstring_ids or self._in_annotation or not node.value:
                self.skipped += 1
                return node
            result = self.encoder.encode(node.value)
            self.protected += 1
            return ast.copy_location(result, node)
        return node
    def visit_JoinedStr(self, node): self.skipped += 1; return node
    def visit_AnnAssign(self, node):
        self._in_annotation = True
        node.annotation = self.visit(node.annotation)
        self._in_annotation = False
        if node.value: node.value = self.visit(node.value)
        return node
    def visit_arg(self, node):
        if node.annotation:
            self._in_annotation = True
            node.annotation = self.visit(node.annotation)
            self._in_annotation = False
        return node
def protect_chr_arithmetic(tree: ast.Module, config: DragonConfig) -> Tuple[int, int]:
    build_seed = config.seed if config.seed is not None else 0
    marker = _DocstringMarker()
    marker.visit(tree)
    encoder = _ChrArithmeticEncoder(build_seed)
    protector = _ChrArithStringProtector(encoder, marker.docstring_node_ids)
    protector.visit(tree)
    ast.fix_missing_locations(tree)
    return protector.protected, protector.skipped
def classify_string(s: str) -> str:
    low = s.lower()
    if s.startswith("http://") or s.startswith("https://"):
        return "url"
    if any(k in low for k in _REQUESTS_SENSITIVE_KEYWORDS):
        return "credential_like"
    if "%s" in s or "%d" in s or "%r" in s or ("{" in s and "}" in s):
        return "format_string"
    if s.isidentifier() and "." not in s:
        return "protocol_token"
    if len(s) <= 24:
        return "user_visible_short"
    return "static_data"
_INTERLEAVE_JUNK_POOLS = (
    "ghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "BCDFGHJKLMNPQRSTVWXZbcdfghjklmnpqrstvwxz0123456789!@",
)
_INTERLEAVE_JUNK_POOL = _INTERLEAVE_JUNK_POOLS[0]
def _encode_interleaved_hex(s: str, rng: "random.Random") -> ast.expr:
    hx = s.encode("utf-8").hex()
    _pool = _INTERLEAVE_JUNK_POOLS[rng.randrange(len(_INTERLEAVE_JUNK_POOLS))]
    _parity = rng.randrange(2)
    if _parity == 0:
        interleaved = "".join(
            hc + _pool[rng.randrange(len(_pool))]
            for hc in hx
        )
        sl = ast.Slice(lower=ast.Constant(value=0), upper=None,
                       step=ast.Constant(value=2))
    else:
        interleaved = "".join(
            _pool[rng.randrange(len(_pool))]
            + hc
            for hc in hx
        )
        sl = ast.Slice(lower=ast.Constant(value=1), upper=None,
                       step=ast.Constant(value=2))
    subscript = ast.Subscript(
        value=ast.Constant(value=interleaved),
        slice=sl,
        ctx=ast.Load(),
    )
    fromhex_call = ast.Call(
        func=ast.Attribute(value=ast.Name(id="bytes", ctx=ast.Load()),
                           attr="fromhex", ctx=ast.Load()),
        args=[subscript], keywords=[],
    )
    return ast.Call(
        func=ast.Attribute(value=fromhex_call, attr="decode", ctx=ast.Load()),
        args=[ast.Constant(value="utf-8")], keywords=[],
    )
def _encode_affine_char(s: str, naming: RuneNaming, key_idx: int, build_seed: int = 0) -> ast.expr:
    _pairs = ((611, 2010), (667, 2048), (701, 1999), (733, 2111))
    a_mul, b_off = _pairs[(int(build_seed) + int(key_idx)) % len(_pairs)]
    param = naming.generate(f"affine::{key_idx}")
    elt = ast.Call(
        func=ast.Name(id="chr", ctx=ast.Load()),
        args=[ast.BinOp(
            left=ast.BinOp(left=ast.Name(id=param, ctx=ast.Load()),
                           op=ast.Sub(),
                           right=ast.Constant(value=b_off)),
            op=ast.FloorDiv(),
            right=ast.Constant(value=a_mul),
        )],
        keywords=[],
    )
    genexp = ast.GeneratorExp(
        elt=elt,
        generators=[ast.comprehension(
            target=ast.Name(id=param, ctx=ast.Store()),
            iter=ast.List(
                elts=[ast.Constant(value=ord(ch) * a_mul + b_off)
                      for ch in s],
                ctx=ast.Load(),
            ),
            ifs=[],
            is_async=0,
        )],
    )
    return ast.Call(
        func=ast.Attribute(value=ast.Constant(value=""), attr="join",
                           ctx=ast.Load()),
        args=[genexp], keywords=[],
    )
class _MixedStringProtector(ast.NodeTransformer):
    LONG_STRING_TABLE_THRESHOLD = 48
    def __init__(self, naming: RuneNaming, docstring_ids: Set[int],
                 build_seed: int, fragment_planner: FragmentPlanner,
                 lookup_entries: List[Tuple[int, bytes]], bytes_key: int,
                 names: Optional[EmberNames] = None):
        self.serpent = _SerpentStringProtector(naming, docstring_ids, build_seed)
        self.chrarith_encoder = _ChrArithmeticEncoder(build_seed)
        self.docstring_ids = docstring_ids
        self.fragment_planner = fragment_planner
        self.lookup_entries = lookup_entries
        self.bytes_key = bytes_key
        self.names = names if names is not None else _legacy_ember_names()
        self._select_rng = _seeded_rng(build_seed, "string_strategy_select")
        self.protected_count = 0
        self.skipped_count = 0
        self._affine_idx = 0
        self.strategy_counts: Dict[str, int] = {
            "serpent": 0, "chr_arithmetic": 0, "ember_table": 0,
            "interleaved_hex": 0, "affine_char": 0}
        self.category_counts: Dict[str, int] = {}
        self._in_annotation = False
    def _protect_ember(self, node: ast.Constant) -> ast.AST:
        data = node.value.encode("utf-8")
        n_fragments = self.fragment_planner.fragment_count_for(len(data))
        fragments = _split_bytes(data, n_fragments)
        indices: List[int] = []
        for frag_idx, frag in enumerate(fragments):
            key, rev_flag, add_mode = self.fragment_planner.spec_for(
                self.serpent._str_idx, frag_idx)
            encoded = _ember_encode_fragment(frag, key, rev_flag, add_mode)
            packed = key | (0x100 if rev_flag else 0) | (0x200 if add_mode else 0)
            table_index = len(self.lookup_entries)
            self.lookup_entries.append((packed, encoded))
            indices.append(table_index)
        self.serpent._str_idx += 1
        call = ast.Call(
            func=ast.Name(id=self.names.lookup_helper, ctx=ast.Load()),
            args=[ast.Tuple(elts=[ast.Constant(value=i) for i in indices], ctx=ast.Load())],
            keywords=[],
        )
        return ast.copy_location(call, node)
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            return self._protect_str(node)
        if isinstance(node.value, (bytes, bytearray)):
            self.serpent._in_annotation = self._in_annotation
            return self.serpent.visit_Constant(node)
        return node
    def _protect_str(self, node: ast.Constant) -> ast.AST:
        if id(node) in self.docstring_ids or self._in_annotation or not node.value:
            self.skipped_count += 1
            return node
        try:
            data_len = len(node.value.encode("utf-8"))
        except (UnicodeEncodeError, AttributeError):
            self.skipped_count += 1
            return node
        if data_len > self.LONG_STRING_TABLE_THRESHOLD:
            result_node = self._protect_ember(node)
            self.strategy_counts["ember_table"] += 1
            self.protected_count += 1
            cat = classify_string(node.value)
            self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
            return result_node
        pick = self._select_rng.randrange(4)
        if pick == 0:
            result_node = self.serpent.visit_Constant(node)
            if result_node is not node:
                self.strategy_counts["serpent"] += 1
                self.protected_count += 1
                cat = classify_string(node.value)
                self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
            else:
                self.skipped_count += 1
            return result_node
        if pick == 1:
            try:
                result = self.chrarith_encoder.encode(node.value)
            except Exception:
                result_node = self.serpent.visit_Constant(node)
                if result_node is not node:
                    self.strategy_counts["serpent"] += 1
                    self.protected_count += 1
                    cat = classify_string(node.value)
                    self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
                else:
                    self.skipped_count += 1
                return result_node
            self.strategy_counts["chr_arithmetic"] += 1
            self.protected_count += 1
            cat = classify_string(node.value)
            self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
            return ast.copy_location(result, node)
        if pick == 2:
            try:
                result = _encode_interleaved_hex(node.value, self._select_rng)
            except Exception:
                result_node = self.serpent.visit_Constant(node)
                if result_node is not node:
                    self.strategy_counts["serpent"] += 1
                    self.protected_count += 1
                    cat = classify_string(node.value)
                    self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
                else:
                    self.skipped_count += 1
                return result_node
            self.strategy_counts["interleaved_hex"] += 1
            self.protected_count += 1
            cat = classify_string(node.value)
            self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
            return ast.copy_location(result, node)
        try:
            result = _encode_affine_char(
                node.value, self.serpent.naming, self._affine_idx,
                self.serpent.build_seed)
            self._affine_idx += 1
        except Exception:
            result_node = self.serpent.visit_Constant(node)
            if result_node is not node:
                self.strategy_counts["serpent"] += 1
                self.protected_count += 1
                cat = classify_string(node.value)
                self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
            else:
                self.skipped_count += 1
            return result_node
        self.strategy_counts["affine_char"] += 1
        self.protected_count += 1
        cat = classify_string(node.value)
        self.category_counts[cat] = self.category_counts.get(cat, 0) + 1
        return ast.copy_location(result, node)
    def visit_JoinedStr(self, node: ast.JoinedStr):
        self.skipped_count += 1
        return node
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._in_annotation = True
        node.annotation = self.visit(node.annotation)
        self._in_annotation = False
        if node.value:
            node.value = self.visit(node.value)
        return node
    def visit_arg(self, node: ast.arg):
        if node.annotation:
            self._in_annotation = True
            node.annotation = self.visit(node.annotation)
            self._in_annotation = False
        return node
def protect_mixed(tree: ast.Module, config: DragonConfig,
                  naming: RuneNaming) -> Tuple[int, int, Dict[str, int], Dict[str, int]]:
    build_seed = config.seed if config.seed is not None else 0
    marker = _DocstringMarker()
    marker.visit(tree)
    fragment_planner = FragmentPlanner(config.expansion_level, build_seed)
    bytes_key = hashlib.sha256(f"{build_seed}::ember_bytes_key".encode()).digest()[0]
    lookup_entries: List[Tuple[int, bytes]] = []
    ember_names = EmberNames(build_seed)
    router = _MixedStringProtector(naming, marker.docstring_node_ids, build_seed,
                                    fragment_planner, lookup_entries, bytes_key,
                                    names=ember_names)
    router.visit(tree)
    ast.fix_missing_locations(tree)
    if lookup_entries:
        _emit_ember_table_helpers(tree, lookup_entries, build_seed, bytes_key,
                                   names=ember_names)
    return (router.protected_count, router.skipped_count,
            router.strategy_counts, router.category_counts)
class _FStringUnsupported(Exception):
    pass
class FStringFormatPass(ast.NodeTransformer):
    _CONV = {None: "", -1: "", 114: "!r", 115: "!s", 97: "!a"}
    def __init__(self):
        self.converted = 0
        self.skipped = 0
        self._in_annotation = False
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._in_annotation += 1
        node.annotation = self.visit(node.annotation)
        self._in_annotation -= 1
        if node.value:
            node.value = self.visit(node.value)
        return node
    def visit_arg(self, node: ast.arg):
        if node.annotation:
            self._in_annotation += 1
            node.annotation = self.visit(node.annotation)
            self._in_annotation -= 1
        return node
    @staticmethod
    def _check_unsafe(expr: ast.expr) -> None:
        for sub in ast.walk(expr):
            if isinstance(sub, (ast.Yield, ast.YieldFrom, ast.Await,
                                ast.Starred)):
                raise _FStringUnsupported
    def _spec_text(self, spec: ast.JoinedStr, args: List[ast.expr]) -> str:
        out: List[str] = []
        for sv in spec.values:
            if isinstance(sv, ast.Constant):
                if not isinstance(sv.value, str):
                    raise _FStringUnsupported
                out.append(sv.value)
            elif isinstance(sv, ast.FormattedValue):
                if sv.format_spec is not None or (
                        sv.conversion not in (None, -1)):
                    raise _FStringUnsupported
                self._check_unsafe(sv.value)
                args.append(self.visit(sv.value))
                out.append("{" + str(len(args) - 1) + "}")
            else:
                raise _FStringUnsupported
        return "".join(out)
    def _convert(self, node: ast.JoinedStr) -> ast.Call:
        fmt_parts: List[str] = []
        args: List[ast.expr] = []
        for val in node.values:
            if isinstance(val, ast.Constant):
                if not isinstance(val.value, str):
                    raise _FStringUnsupported
                fmt_parts.append(
                    val.value.replace("{", "{{").replace("}", "}}"))
            elif isinstance(val, ast.FormattedValue):
                conv = self._CONV.get(val.conversion)
                if conv is None:
                    raise _FStringUnsupported
                self._check_unsafe(val.value)
                args.append(self.visit(val.value))
                piece = "{" + str(len(args) - 1) + conv
                if val.format_spec is not None:
                    if isinstance(val.format_spec, ast.Constant):
                        if not isinstance(val.format_spec.value, str):
                            raise _FStringUnsupported
                        piece += ":" + val.format_spec.value
                    elif isinstance(val.format_spec, ast.JoinedStr):
                        piece += ":" + self._spec_text(val.format_spec, args)
                    else:
                        raise _FStringUnsupported
                fmt_parts.append(piece + "}")
            else:
                raise _FStringUnsupported
        call = ast.Call(
            func=ast.Attribute(value=ast.Constant(value="".join(fmt_parts)),
                               attr="format", ctx=ast.Load()),
            args=args, keywords=[],
        )
        return ast.copy_location(call, node)
    def visit_JoinedStr(self, node: ast.JoinedStr):
        if self._in_annotation:
            return node
        try:
            result = self._convert(node)
        except _FStringUnsupported:
            self.skipped += 1
            return node
        self.converted += 1
        return result
def run_fstring_split(tree: ast.Module) -> Tuple[int, int]:
    fs_pass = FStringFormatPass()
    fs_pass.visit(tree)
    ast.fix_missing_locations(tree)
    return fs_pass.converted, fs_pass.skipped
class ConstantProtector(_SkipGeneratedTransformer):
    def __init__(self, build_seed: int, expansion_level: str,
                 int_chain_max_depth: int = 3, bool_coverage: float = 0.85):
        self.rng = _seeded_rng(build_seed, "constant_protect")
        self.strategy_rng = _seeded_rng(build_seed, "constant_strategy_select")
        self.expansion_level = expansion_level
        self.int_chain_max_depth = max(1, min(4, int_chain_max_depth))
        self.bool_coverage = min(1.0, max(0.0, bool_coverage))
        self.protected_count = 0
        self.skipped_count = 0
        self.strategy_counts: Dict[str, int] = {"arithmetic": 0, "xor": 0, "chain": 0,
                                                 "bool": 0, "mul": 0, "float": 0}
        self._in_annotation = False
        self._in_slice = False
        self._compare_is_operands: Set[int] = set()
        self._match_pattern_const_ids: Set[int] = set()
    def _collect_is_operands(self, tree: ast.AST):
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for op, operand in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.Is, ast.IsNot)):
                        for o in (node.left, operand):
                            self._compare_is_operands.add(id(o))
            if isinstance(node, ast.Match):
                for h in node.cases:
                    for sub in ast.walk(h.pattern):
                        if isinstance(sub, ast.Constant):
                            self._match_pattern_const_ids.add(id(sub))
    def visit_Module(self, node: ast.Module):
        self._collect_is_operands(node)
        self.generic_visit(node)
        return node
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._in_annotation = True
        node.annotation = self.visit(node.annotation)
        self._in_annotation = False
        if node.value:
            node.value = self.visit(node.value)
        return node
    def visit_arg(self, node: ast.arg):
        if node.annotation:
            self._in_annotation = True
            node.annotation = self.visit(node.annotation)
            self._in_annotation = False
        return node
    def visit_Slice(self, node: ast.Slice):
        self._in_slice = True
        self.generic_visit(node)
        self._in_slice = False
        return node
    def visit_match_case(self, node):
        return node
    def visit_Constant(self, node: ast.Constant):
        if self._in_annotation or self._in_slice:
            self.skipped_count += 1
            return node
        if (id(node) in self._compare_is_operands
                or id(node) in self._match_pattern_const_ids):
            self.skipped_count += 1
            return node
        if isinstance(node.value, bool):
            return self._protect_bool(node)
        if isinstance(node.value, int):
            return self._protect_int(node)
        if isinstance(node.value, float):
            return self._protect_float(node)
        return node
    def _protect_bool(self, node: ast.Constant) -> ast.AST:
        if self.rng.random() > self.bool_coverage or id(node) in self._compare_is_operands:
            self.skipped_count += 1
            return node
        true_forms = [
            lambda: ast.Compare(left=ast.Constant(value=1), ops=[ast.Eq()], comparators=[ast.Constant(value=1)]),
            lambda: ast.Compare(left=ast.Constant(value=0), ops=[ast.Lt()], comparators=[ast.Constant(value=1)]),
            lambda: ast.UnaryOp(op=ast.Not(), operand=ast.Constant(value=0)),
            lambda: ast.Compare(left=ast.Constant(value=2), ops=[ast.GtE()], comparators=[ast.Constant(value=1)]),
        ]
        false_forms = [
            lambda: ast.Compare(left=ast.Constant(value=1), ops=[ast.NotEq()], comparators=[ast.Constant(value=1)]),
            lambda: ast.Compare(left=ast.Constant(value=1), ops=[ast.Lt()], comparators=[ast.Constant(value=0)]),
            lambda: ast.UnaryOp(op=ast.Not(), operand=ast.Constant(value=1)),
            lambda: ast.Compare(left=ast.Constant(value=1), ops=[ast.GtE()], comparators=[ast.Constant(value=2)]),
        ]
        forms = true_forms if node.value else false_forms
        expr = forms[self.rng.randrange(len(forms))]()
        self.strategy_counts["bool"] += 1
        self.protected_count += 1
        return ast.copy_location(expr, node)
    def _protect_int(self, node: ast.Constant) -> ast.AST:
        value = node.value
        if -1 <= value <= 1:
            self.skipped_count += 1
            return node
        r = self.strategy_rng.random()
        depth = 1
        if self.int_chain_max_depth >= 2 and r < 0.45:
            depth = 2
        if self.int_chain_max_depth >= 3 and r < 0.18:
            depth = 3
        if self.int_chain_max_depth >= 4 and r < 0.06:
            depth = 4
        expr, computed, top_op = self._build_int_expr(value, depth)
        if computed != value:
            expr = self._build_arithmetic_expr(value)
            self.strategy_counts["arithmetic"] += 1
        elif depth > 1:
            self.strategy_counts["chain"] += 1
        elif top_op == 0:
            self.strategy_counts["xor"] += 1
        elif top_op == 3:
            self.strategy_counts["mul"] += 1
        else:
            self.strategy_counts["arithmetic"] += 1
        self.protected_count += 1
        return ast.copy_location(expr, node)
    def _build_int_expr(self, value: int, depth: int) -> Tuple[ast.expr, int, int]:
        if depth <= 0:
            return ast.Constant(value=value), value, -1
        op = self.rng.randrange(4)
        if op == 3:
            if value != 0:
                a = self.rng.choice((2, 3, 5, 7, 11, 13, 17))
                if value % a == 0:
                    child_expr, child_val, _ = self._build_int_expr(value // a, depth - 1)
                    return (ast.BinOp(left=child_expr, op=ast.Mult(),
                                      right=ast.Constant(value=a)),
                            child_val * a, op)
            op = 0
        if op == 0:
            k = self.rng.randint(1, max(16, abs(value) * 2 + 7))
            enc = value ^ k
            child_expr, child_val, _ = self._build_int_expr(enc, depth - 1)
            return ast.BinOp(left=child_expr, op=ast.BitXor(),
                             right=ast.Constant(value=k)), child_val ^ k, op
        if op == 1:
            split = self.rng.randint(1, max(1, abs(value)))
            a = split if value >= 0 else -split
            child = value - a
            child_expr, child_val, _ = self._build_int_expr(child, depth - 1)
            return ast.BinOp(left=child_expr, op=ast.Add(),
                             right=ast.Constant(value=a)), child_val + a, op
        k = self.rng.randint(1, max(16, abs(value) * 2 + 7))
        child = value + k
        child_expr, child_val, _ = self._build_int_expr(child, depth - 1)
        return ast.BinOp(left=child_expr, op=ast.Sub(),
                         right=ast.Constant(value=k)), child_val - k, op
    def _build_arithmetic_expr(self, value: int) -> ast.BinOp:
        split = self.rng.randint(1, max(1, abs(value)))
        a = split if value >= 0 else -split
        b = value - a
        return ast.BinOp(left=ast.Constant(value=a), op=ast.Add(), right=ast.Constant(value=b))
    def _protect_float(self, node: ast.Constant) -> ast.AST:
        value = node.value
        if value != value or value in (float("inf"), float("-inf")):
            self.skipped_count += 1
            return node
        if self.rng.random() < 0.5:
            m, e = math.frexp(value)
            if m * (2.0 ** e) == value:
                expr = ast.BinOp(
                    left=ast.Constant(value=m), op=ast.Mult(),
                    right=ast.BinOp(left=ast.Constant(value=2.0),
                                    op=ast.Pow(), right=ast.Constant(value=e)))
                self.strategy_counts["float"] += 1
                self.protected_count += 1
                return ast.copy_location(expr, node)
        a = float(int(value))
        b = value - a
        if (a + b) != value:
            self.skipped_count += 1
            return node
        if a == 0.0:
            self.skipped_count += 1
            return node
        expr = ast.BinOp(left=ast.Constant(value=a), op=ast.Add(), right=ast.Constant(value=b))
        self.strategy_counts["float"] += 1
        self.protected_count += 1
        return ast.copy_location(expr, node)
def protect_constants(tree: ast.Module, config: DragonConfig,
                      int_chain_max_depth: int = 3,
                      bool_coverage: float = 0.85) -> Tuple[int, int, Dict[str, int]]:
    _empty_counts: Dict[str, int] = {"arithmetic": 0, "xor": 0, "chain": 0,
                                     "bool": 0, "mul": 0, "float": 0}
    try:
        _has_cand = False
        for _n in ast.walk(tree):
            if isinstance(_n, ast.Constant) and isinstance(
                    _n.value, (int, float, bool)):
                _has_cand = True
                break
        if not _has_cand:
            return 0, 0, dict(_empty_counts)
    except Exception:
        pass
    build_seed = config.seed if config.seed is not None else 0
    protector = ConstantProtector(build_seed, config.expansion_level,
                                   int_chain_max_depth=int_chain_max_depth,
                                   bool_coverage=bool_coverage)
    protector.visit(tree)
    ast.fix_missing_locations(tree)
    return protector.protected_count, protector.skipped_count, protector.strategy_counts
class IntHidePass(_SkipGeneratedTransformer):
    name = "int_hide"
    def __init__(self, naming: RuneNaming, build_seed: int,
                 coverage: float = 0.9, min_abs: int = 256,
                 cap: int = 400):
        self.naming = naming
        self.build_seed = build_seed
        self.coverage = min(1.0, max(0.05, coverage))
        self.min_abs = max(2, int(min_abs))
        self.cap = max(0, int(cap))
        self.rng = _seeded_rng(build_seed, "int_hide_select")
        self._in_annotation = 0
        self._in_slice = 0
        self._in_fstring = 0
        self._is_operands: Set[int] = set()
        self.helper = ""
        self.big = 0
        self.protected_count = 0
    def _collect_is_operands(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for op, operand in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.Is, ast.IsNot)):
                        self._is_operands.add(id(node.left))
                        self._is_operands.add(id(operand))
            if isinstance(node, ast.Match):
                for h in node.cases:
                    for sub in ast.walk(h.pattern):
                        if isinstance(sub, ast.Constant):
                            self._is_operands.add(id(sub))
    def visit_Module(self, node: ast.Module):
        self._collect_is_operands(node)
        self.generic_visit(node)
        return node
    def visit_JoinedStr(self, node: ast.JoinedStr):
        self._in_fstring += 1
        self.generic_visit(node)
        self._in_fstring -= 1
        return node
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._in_annotation += 1
        node.annotation = self.visit(node.annotation)
        self._in_annotation -= 1
        if node.value:
            node.value = self.visit(node.value)
        return node
    def visit_arg(self, node: ast.arg):
        if node.annotation:
            self._in_annotation += 1
            node.annotation = self.visit(node.annotation)
            self._in_annotation -= 1
        return node
    def visit_Slice(self, node: ast.Slice):
        self._in_slice += 1
        self.generic_visit(node)
        self._in_slice -= 1
        return node
    def visit_match_case(self, node):
        return node
    def visit_Constant(self, node: ast.Constant):
        if (self._in_annotation or self._in_slice or self._in_fstring
                or id(node) in self._is_operands
                or self.protected_count >= self.cap
                or is_generated(node)):
            return node
        v = node.value
        if isinstance(v, bool) or not isinstance(v, int):
            return node
        if abs(v) < self.min_abs:
            return node
        if self.rng.random() > self.coverage:
            return node
        new = _mkcall(self.helper, [ast.Constant(value=self.big + v)])
        mark_generated(new)
        ast.copy_location(new, node)
        self.protected_count += 1
        return new
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        del rng
        digest = hashlib.sha256(
            f"{self.build_seed}::int_hide_big".encode()).hexdigest()
        self.big = 10 ** 56 + int(digest[:14], 16) % 10 ** 56
        self.helper = _gen_unique(
            self.naming, "int_hide::H", _used_identifiers(tree))
        self.visit(tree)
        if self.protected_count > 0:
            lam = ast.Assign(
                targets=[ast.Name(id=self.helper, ctx=ast.Store())],
                value=ast.Lambda(
                    args=ast.arguments(
                        posonlyargs=[], args=[ast.arg(arg="x")],
                        vararg=None, kwonlyargs=[], kw_defaults=[],
                        kwarg=None, defaults=[]),
                    body=_mkcall("int", [ast.BinOp(
                        left=ast.Name(id="x", ctx=ast.Load()),
                        op=ast.Sub(),
                        right=ast.Constant(value=self.big))])))
            mark_generated(lam)
            ast.fix_missing_locations(lam)
            tree.body.insert(_module_insert_index(tree), lam)
        return self.protected_count
class IntPoolIndirectionPass(_SkipGeneratedTransformer):
    name = "int_pool_indirection"
    def __init__(self, naming: RuneNaming, build_seed: int,
                 coverage: float = 0.9, min_abs: int = 320,
                 cap: int = 500):
        self.naming = naming
        self.build_seed = build_seed
        self.coverage = min(1.0, max(0.05, coverage))
        self.min_abs = max(2, int(min_abs))
        self.cap = max(0, int(cap))
        self.rng = _seeded_rng(build_seed, "int_pool_select")
        self._in_annotation = 0
        self._in_slice = 0
        self._in_fstring = 0
        self._is_operands: Set[int] = set()
        self.pool_name = ""
        self.entries: List[int] = []
        self.protected_count = 0
    def _collect_is_operands(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for op, operand in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.Is, ast.IsNot)):
                        self._is_operands.add(id(node.left))
                        self._is_operands.add(id(operand))
            if isinstance(node, ast.Match):
                for h in node.cases:
                    for sub in ast.walk(h.pattern):
                        if isinstance(sub, ast.Constant):
                            self._is_operands.add(id(sub))
    def visit_Module(self, node: ast.Module):
        self._collect_is_operands(node)
        self.generic_visit(node)
        return node
    def visit_JoinedStr(self, node: ast.JoinedStr):
        self._in_fstring += 1
        self.generic_visit(node)
        self._in_fstring -= 1
        return node
    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._in_annotation += 1
        node.annotation = self.visit(node.annotation)
        self._in_annotation -= 1
        if node.value:
            node.value = self.visit(node.value)
        return node
    def visit_arg(self, node: ast.arg):
        if node.annotation:
            self._in_annotation += 1
            node.annotation = self.visit(node.annotation)
            self._in_annotation -= 1
        return node
    def visit_Slice(self, node: ast.Slice):
        self._in_slice += 1
        self.generic_visit(node)
        self._in_slice -= 1
        return node
    def visit_match_case(self, node):
        return node
    def visit_Constant(self, node: ast.Constant):
        if (self._in_annotation or self._in_slice or self._in_fstring
                or id(node) in self._is_operands
                or self.protected_count >= self.cap
                or is_generated(node)):
            return node
        v = node.value
        if isinstance(v, bool) or not isinstance(v, int):
            return node
        if abs(v) < self.min_abs:
            return node
        if self.rng.random() > self.coverage:
            return node
        idx = self.protected_count
        kd = hashlib.sha256(
            f"{self.build_seed}::int_pool::{idx}".encode()).digest()
        k1, k2 = kd[0] | 1, kd[1] | 1
        sub = ast.Subscript(
            value=ast.Name(id=self.pool_name, ctx=ast.Load()),
            slice=ast.Constant(value=idx),
            ctx=ast.Load(),
        )
        expr = ast.BinOp(left=sub, op=ast.BitXor(),
                         right=ast.BinOp(left=ast.Constant(value=k1),
                                         op=ast.BitXor(),
                                         right=ast.Constant(value=k2)))
        self.entries.append(v ^ k1 ^ k2)
        self.protected_count += 1
        mark_generated(expr)
        return ast.copy_location(expr, node)
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        self.pool_name = self.naming.generate(f"ipool::{self.build_seed}")
        self.entries = []
        self.protected_count = 0
        self.visit(tree)
        if not self.entries:
            return 0
        assign = ast.parse(
            f"{self.pool_name} = {tuple(self.entries)!r}").body[0]
        mark_generated(assign)
        ast.fix_missing_locations(assign)
        tree.body.insert(_module_insert_index(tree), assign)
        ast.fix_missing_locations(tree)
        return self.protected_count
_HTTP_LIB_ROOTS = (
    "requests", "urllib", "urllib3", "httpx", "aiohttp", "http",
)
_REQUESTS_SENSITIVE_KEYWORDS = (
    "authorization", "api_key", "api-key", "apikey", "token", "secret",
    "bearer", "cookie", "password", "passwd", "credential", "auth",
    "session", "csrf", "xsrf", "signature", "webhook", "private",
)
class _RequestsImportDetector(ast.NodeVisitor):
    def __init__(self):
        self.found = False
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _HTTP_LIB_ROOTS:
                self.found = True
        self.generic_visit(node)
    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module and node.module.split(".")[0] in _HTTP_LIB_ROOTS:
            self.found = True
        self.generic_visit(node)
_RETALIATION_SPAM_LOOP_MIN = 100_000
_RETALIATION_BROWSER_MARKERS = ("http://", "https://", "xdg-open",
                                "start ", "start", "open ")
_RETALIATION_BSOD_NAMES = {"NtRaiseHardError", "RtlAdjustPrivilege"}
def _dotted_name(node: ast.AST) -> str:
    parts: List[str] = []
    cur: Optional[ast.AST] = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    if parts:
        parts.reverse()
        return "?." + ".".join(parts)
    return "?"
def _subtree_has_file(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "__file__":
            return True
    return False
def _const_strs(node: ast.AST) -> List[str]:
    out: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out
def _call_str_args(node: ast.Call) -> List[str]:
    out: List[str] = []
    for a in list(node.args) + [kw.value for kw in node.keywords
                                if isinstance(kw, ast.keyword)]:
        out.extend(_const_strs(a))
    return out
class _RetaliationScanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: List[Tuple[str, int, str]] = []
    def _add(self, severity: str, node: ast.AST, message: str) -> None:
        self.findings.append(
            (severity, int(getattr(node, "lineno", 0) or 0), message))
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] == "webbrowser":
                self._add("medium", node,
                          "webbrowser import (review: bulk-open loops)")
        self.generic_visit(node)
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.split(".")[0] == "webbrowser":
            self._add("medium", node,
                      "webbrowser import (review: bulk-open loops)")
        self.generic_visit(node)
    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _RETALIATION_BSOD_NAMES:
            self._add("critical", node,
                      f"BSOD primitive referenced: {node.id}")
        self.generic_visit(node)
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "windll":
            self._add("high", node,
                      "ctypes.windll access (review: native privilege/BSOD)")
        elif node.attr in _RETALIATION_BSOD_NAMES:
            self._add("critical", node,
                      f"BSOD primitive referenced: {node.attr}")
        self.generic_visit(node)
    def visit_Call(self, node: ast.Call) -> None:
        dotted = _dotted_name(node.func)
        base = dotted.split(".")[-1]
        args = list(node.args) + [kw.value for kw in node.keywords
                                  if isinstance(kw, ast.keyword)]
        if base in ("remove", "unlink", "rmdir", "rmtree"):
            if _subtree_has_file(node):
                self._add("critical", node,
                          f"self-delete shape: {dotted}(... __file__ ...)")
        if base in ("system", "popen", "Popen", "call", "run",
                    "check_call", "check_output"):
            joined = "\n".join(_call_str_args(node))
            if any(m in joined for m in _RETALIATION_BROWSER_MARKERS):
                self._add("critical", node,
                          f"browser-spam shape: {dotted} with URL/opener arg")
        if dotted.startswith("webbrowser.") and base.startswith("open"):
            self._add("high", node,
                      f"programmatic browser open: {dotted}")
        self.generic_visit(node)
    def _loop_spam_shape(self, node: ast.AST) -> bool:
        it: Optional[ast.AST] = None
        if isinstance(node, ast.For):
            it = node.iter
        elif isinstance(node, ast.While):
            return False
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and it.func.id == "range" and len(it.args) == 1
                and isinstance(it.args[0], ast.Constant)
                and isinstance(it.args[0].value, int)
                and it.args[0].value >= _RETALIATION_SPAM_LOOP_MIN):
            return False
        for sub in ast.walk(node):
            if sub is node:
                continue
            if isinstance(sub, ast.Call):
                base = _dotted_name(sub.func).split(".")[-1]
                if base in ("system", "popen", "Popen", "call", "run",
                            "check_call", "check_output"):
                    return True
        return False
    def visit_For(self, node: ast.For) -> None:
        if self._loop_spam_shape(node):
            self._add("critical", node,
                      "spam-loop shape: range(>=100000) driving shell-exec")
        self.generic_visit(node)
    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Mult):
            big = False
            if isinstance(node.right, ast.Constant) and isinstance(
                    node.right.value, int) and node.right.value >= 10 ** 9:
                big = True
            if big and isinstance(node.left, (ast.List, ast.Tuple, ast.Set)):
                self._add("high", node,
                          "memory-bomb shape: container * huge-count")
            if big and isinstance(node.left, ast.Constant) and isinstance(
                    node.left.value, str):
                self._add("high", node,
                          "memory-bomb shape: str * huge-count")
        self.generic_visit(node)
def scan_retaliation(tree: ast.AST) -> List[Tuple[str, int, str]]:
    scanner = _RetaliationScanner()
    scanner.visit(tree)
    return scanner.findings
def _looks_like_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "ws://", "wss://"))
def _looks_like_sensitive_key(s: str) -> bool:
    lowered = s.lower()
    return any(kw in lowered for kw in _REQUESTS_SENSITIVE_KEYWORDS)
def _looks_like_secret_value(s: str) -> bool:
    import re as _re
    if len(s) < 20:
        return False
    if s.startswith(("-----BEGIN", "eyJ")):
        return True
    probes = (
        r"^AKIA[0-9A-Z]{16}$",
        r"^xox[baprs]-",
        r"^[A-Fa-f0-9]{32,}$",
        r"^[A-Za-z0-9+/_=-]{40,}$",
        r"^[a-z]+://[^:/@]+:[^@/]+@",
    )
    return any(_re.match(p, s) for p in probes)
class _RequestsStringProtector(ast.NodeTransformer):
    def __init__(self, naming: RuneNaming, build_seed: int,
                 already_protected_ids: Set[int]):
        self.naming = naming
        self.build_seed = build_seed
        self.already_protected_ids = set(already_protected_ids)
        self.protected_count = 0
        self._str_idx = 0
        self.targets: Set[int] = set()
    def _mark_subtree_strings(self, node: ast.AST) -> None:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str) and sub.value):
                self.targets.add(id(sub))
    def _collect(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant)
                            and isinstance(k.value, str)
                            and _looks_like_sensitive_key(k.value)):
                        self._mark_subtree_strings(v)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value:
                s = node.value
                if (_looks_like_url(s) or _looks_like_sensitive_key(s)
                        or _looks_like_secret_value(s)):
                    self.targets.add(id(node))
    def _encode(self, s: str) -> ast.expr:
        data = s.encode("utf-8")
        elts: List[ast.expr] = []
        idx = self._str_idx
        self._str_idx += 1
        for bi, b in enumerate(data):
            kd = hashlib.sha256(
                f"{self.build_seed}::requests_protect::{idx}::{bi}".encode()
            ).digest()
            k1, k2 = kd[0], kd[1] | 1
            enc = b ^ k1 ^ k2
            param = self.naming.generate(f"rp::{idx}::{bi}::{self.build_seed}")
            lam = ast.Lambda(
                args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=param)],
                                   kwonlyargs=[], kw_defaults=[], defaults=[],
                                   vararg=None, kwarg=None),
                body=ast.BinOp(left=ast.Name(id=param, ctx=ast.Load()),
                               op=ast.BitXor(),
                               right=ast.BinOp(left=ast.Constant(value=k1),
                                               op=ast.BitXor(),
                                               right=ast.Constant(value=k2))),
            )
            elts.append(ast.Call(func=lam, args=[ast.Constant(value=enc)], keywords=[]))
        bytes_call = ast.Call(func=ast.Name(id="bytes", ctx=ast.Load()),
                              args=[ast.List(elts=elts, ctx=ast.Load())], keywords=[])
        return ast.Call(
            func=ast.Attribute(value=bytes_call, attr="decode", ctx=ast.Load()),
            args=[ast.Constant(value="utf-8")], keywords=[],
        )
    def prepare(self, tree: ast.Module) -> None:
        self._collect(tree)
    def visit_Constant(self, node: ast.Constant):
        if not isinstance(node.value, str) or not node.value:
            return node
        if id(node) in self.already_protected_ids:
            return node
        if id(node) in self.targets:
            result = self._encode(node.value)
            self.protected_count += 1
            return ast.copy_location(result, node)
        return node
    def visit_JoinedStr(self, node):
        return node
    def visit_match_case(self, node):
        return node
def detect_requests_import(tree: ast.Module) -> bool:
    detector = _RequestsImportDetector()
    detector.visit(tree)
    return detector.found
def protect_requests_literals(tree: ast.Module, config: DragonConfig,
                              naming: RuneNaming) -> int:
    if not detect_requests_import(tree):
        return 0
    build_seed = config.seed if config.seed is not None else 0
    protector = _RequestsStringProtector(naming, build_seed, set())
    protector.prepare(tree)
    protector.visit(tree)
    ast.fix_missing_locations(tree)
    return protector.protected_count
SIMPLE_STMT_TYPES = (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Pass)
UNSUPPORTED_NODE_TYPES = (
    ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
    ast.AsyncWith, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
)
def _contains_suspend_or_jump(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Yield, ast.YieldFrom, ast.Await,
                             ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return True
    return False
def _is_straight_line_block(body: List[ast.stmt]) -> bool:
    if not body:
        return False
    for stmt in body:
        if isinstance(stmt, UNSUPPORTED_NODE_TYPES) or isinstance(stmt, ast.If):
            return False
        if not isinstance(stmt, SIMPLE_STMT_TYPES):
            return False
        if _contains_suspend_or_jump(stmt):
            return False
    return True
def _is_simple_if_chain_branch(body: List[ast.stmt]) -> bool:
    if not body:
        return False
    for stmt in body:
        if isinstance(stmt, (UNSUPPORTED_NODE_TYPES + (ast.If,))):
            return False
        if not isinstance(stmt, SIMPLE_STMT_TYPES):
            return False
        if _contains_suspend_or_jump(stmt):
            return False
    return True
def _collect_if_chain(node: ast.If) -> Optional[List[Tuple[Optional[ast.expr], List[ast.stmt]]]]:
    branches: List[Tuple[Optional[ast.expr], List[ast.stmt]]] = []
    current: ast.stmt = node
    while True:
        if isinstance(current, ast.If):
            if not _is_simple_if_chain_branch(current.body):
                return None
            branches.append((current.test, current.body))
            if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                current = current.orelse[0]
                continue
            elif current.orelse:
                if not _is_simple_if_chain_branch(current.orelse):
                    return None
                branches.append((None, current.orelse))
            break
        else:
            return None
    return branches
def _has_bare_break(stmts: List[ast.stmt]) -> bool:
    def _bare(node: ast.AST) -> bool:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.Lambda, ast.For, ast.AsyncFor,
                               ast.While)):
                continue
            if isinstance(ch, (ast.Break, ast.Continue)):
                return True
            if _bare(ch):
                return True
        return False
    return any(_bare(s) for s in stmts)
_DECOY_ALIASES = ("decoy", "spare", "tmpv", "auxv", "shadow", "extra")
def _decoy_name(var: str) -> str:
    h = hashlib.sha256(f"{var}::decoy_alias".encode()).hexdigest()
    return f"{var}_{_DECOY_ALIASES[int(h[:2], 16) % len(_DECOY_ALIASES)]}{h[2:8]}"
def _loop_test(rng=None) -> ast.expr:
    pick = rng.randrange(3) if rng is not None else 0
    if pick == 1:
        return ast.Compare(left=ast.Constant(value=1),
                           ops=[ast.Eq()], comparators=[ast.Constant(value=1)])
    if pick == 2:
        return ast.UnaryOp(op=ast.Not(), operand=ast.Compare(
            left=ast.Constant(value=1),
            ops=[ast.NotEq()], comparators=[ast.Constant(value=1)]))
    return ast.Constant(value=True)
def _build_loop_dispatcher(branches: List[Tuple[Optional[ast.expr], List[ast.stmt]]],
                           idx_var: str,
                           rng: Optional["random.Random"] = None) -> List[ast.stmt]:
    n = len(branches)
    has_else = branches[-1][0] is None
    default_idx = (n - 1) if has_else else n
    expr: ast.expr = ast.Constant(value=default_idx)
    order = list(enumerate(branches))
    if has_else:
        order = order[:-1]
    for i, (test, _body) in reversed(order):
        expr = ast.IfExp(test=test, body=ast.Constant(value=i), orelse=expr)
    assign_idx = ast.Assign(targets=[ast.Name(id=idx_var, ctx=ast.Store())], value=expr)
    _order = [(i, t, b) for i, (t, b) in enumerate(branches) if t is not None]
    if rng is not None:
        rng.shuffle(_order)
    middle: List[Tuple[ast.expr, List[ast.stmt]]] = [
        (ast.Compare(left=ast.Name(id=idx_var, ctx=ast.Load()),
                     ops=[ast.Eq()], comparators=[ast.Constant(value=i)]),
         list(body) + [ast.Break()])
        for i, _t, body in _order
    ]
    _decoy_idx = n + 997
    _decoy_tgt = _decoy_name(idx_var)
    _decoy_assign = ast.Assign(targets=[ast.Name(id=_decoy_tgt, ctx=ast.Store())],
                               value=ast.Constant(value=(_decoy_idx * 31) & 4095))
    mark_generated(_decoy_assign)
    middle.append((
        ast.Compare(left=ast.Name(id=idx_var, ctx=ast.Load()),
                    ops=[ast.Eq()], comparators=[ast.Constant(value=_decoy_idx)]),
        [_decoy_assign, ast.Break()],
    ))
    if has_else:
        tail: List[ast.stmt] = list(branches[-1][1]) + [ast.Break()]
    else:
        tail = [ast.Break()]
    for _test, _bb in reversed(middle):
        tail = [ast.If(test=_test, body=_bb, orelse=tail)]
    loop = ast.While(test=_loop_test(rng), body=tail, orelse=[])
    return [assign_idx, loop]
def _build_branch_dispatcher(branches: List[Tuple[Optional[ast.expr], List[ast.stmt]]],
                             idx_var: str,
                             rng: Optional["random.Random"] = None) -> List[ast.stmt]:
    n = len(branches)
    if rng is not None and rng.random() < 0.35:
        if not any(_has_bare_break(b) for _, b in branches):
            return _build_loop_dispatcher(branches, idx_var, rng)
    has_else = branches[-1][0] is None
    default_idx = (n - 1) if has_else else n
    expr: ast.expr = ast.Constant(value=default_idx)
    order = list(enumerate(branches))
    if has_else:
        order = order[:-1]
    for i, (test, _body) in reversed(order):
        expr = ast.IfExp(test=test, body=ast.Constant(value=i), orelse=expr)
    assign_idx = ast.Assign(targets=[ast.Name(id=idx_var, ctx=ast.Store())], value=expr)
    _order = list(enumerate(branches))
    if rng is not None:
        rng.shuffle(_order)
    dispatch_stmts: List[ast.stmt] = []
    for i, (_test, body) in _order:
        cond = ast.Compare(left=ast.Name(id=idx_var, ctx=ast.Load()),
                            ops=[ast.Eq()], comparators=[ast.Constant(value=i)])
        dispatch_stmts.append(ast.If(test=cond, body=list(body), orelse=[]))
    _decoy_idx = n + 997
    _decoy_tgt = _decoy_name(idx_var)
    _decoy_assign = ast.Assign(targets=[ast.Name(id=_decoy_tgt, ctx=ast.Store())],
                               value=ast.Constant(value=(_decoy_idx * 31) & 4095))
    mark_generated(_decoy_assign)
    _decoy_cond = ast.Compare(left=ast.Name(id=idx_var, ctx=ast.Load()),
                              ops=[ast.Eq()], comparators=[ast.Constant(value=_decoy_idx)])
    dispatch_stmts.append(ast.If(test=_decoy_cond, body=[_decoy_assign], orelse=[]))
    return [assign_idx] + dispatch_stmts
def _build_straight_line_dispatcher(body: List[ast.stmt],
                                     rng: Optional["random.Random"] = None,
                                     state_var: str = "_wyrm_state") -> List[ast.stmt]:
    n = len(body)
    if rng is not None:
        pool = rng.sample(range(0, max(16, (n + 1) * 8)), n + 2)
        ids = pool[:n]
        end_id = pool[n]
        decoy_id = pool[n + 1]
        while decoy_id in set(ids) | {end_id}:
            decoy_id = (decoy_id + 7919) % max(32, (n + 1) * 16)
        start_id = ids[0]
        trans_mask = rng.randrange(1, 1 << 20)
    else:
        ids = list(range(n))
        end_id = n
        decoy_id = n + 997
        start_id = 0
        trans_mask = 0
    def _mba_next(next_id: int, _rng2: Optional["random.Random"]) -> ast.expr:
        if trans_mask == 0 or rng is None:
            return ast.Constant(value=next_id)
        c1 = ((_rng2.getrandbits(12) if _rng2 is not None else trans_mask) % 4095) + 1
        c2 = ((_rng2.getrandbits(12) if _rng2 is not None else (trans_mask >> 3)) % 4096)
        _n = ast.Constant(value=next_id)
        _or = ast.BinOp(left=_n, op=ast.BitOr(), right=ast.Constant(value=c1))
        _and = ast.BinOp(left=ast.copy_location(ast.Constant(value=next_id), _n),
                         op=ast.BitAnd(), right=ast.Constant(value=c1))
        _add = ast.BinOp(left=_or, op=ast.Add(), right=_and)
        _add2 = ast.BinOp(left=_add, op=ast.Add(), right=ast.Constant(value=c2))
        _sub1 = ast.BinOp(left=_add2, op=ast.Sub(), right=ast.Constant(value=c1))
        return ast.BinOp(left=_sub1, op=ast.Sub(), right=ast.Constant(value=c2))
    new_body: List[ast.stmt] = [
        ast.Assign(targets=[ast.Name(id=state_var, ctx=ast.Store())],
                   value=ast.Constant(value=start_id))
    ]
    next_id = end_id
    current_or_else: List[ast.stmt] = []
    for idx in range(n - 1, -1, -1):
        stmt = body[idx]
        advance = ast.Assign(
            targets=[ast.Name(id=state_var, ctx=ast.Store())],
            value=_mba_next(next_id, rng),
        )
        branch_body = [stmt, advance]
        test = ast.Compare(
            left=ast.Name(id=state_var, ctx=ast.Load()),
            ops=[ast.Eq()],
            comparators=[ast.Constant(value=ids[idx])],
        )
        current_or_else = [ast.If(test=test, body=branch_body, orelse=current_or_else)]
        next_id = ids[idx]
    _decoy_junk = ast.Assign(
        targets=[ast.Name(id=_decoy_name(state_var), ctx=ast.Store())],
        value=ast.Constant(value=trans_mask ^ 0x5A5A if trans_mask else 37337))
    mark_generated(_decoy_junk)
    _decoy_adv = ast.Assign(
        targets=[ast.Name(id=state_var, ctx=ast.Store())],
        value=ast.Constant(value=end_id))
    mark_generated(_decoy_adv)
    _decoy_test = ast.Compare(left=ast.Name(id=state_var, ctx=ast.Load()),
                              ops=[ast.Eq()], comparators=[ast.Constant(value=decoy_id)])
    current_or_else = [ast.If(test=_decoy_test,
                              body=[_decoy_junk, _decoy_adv],
                              orelse=current_or_else)]
    while_loop = ast.While(
        test=ast.Compare(
            left=ast.Name(id=state_var, ctx=ast.Load()),
            ops=[ast.NotEq()],
            comparators=[ast.Constant(value=end_id)],
        ),
        body=current_or_else,
        orelse=[],
    )
    new_body.append(while_loop)
    return new_body
def flatten_eligible_blocks(tree: ast.Module, build_seed: int = 0) -> Tuple[int, int]:
    straight_line_count = 0
    if_chain_count = 0
    _nrng = _seeded_rng(build_seed, "wyrm_state_names")
    state_name = f"_ws{_nrng.getrandbits(48):012x}"
    branch_name = f"_wb{_nrng.getrandbits(48):012x}"
    walk_counter = [0]
    class _Walker(ast.NodeTransformer):
        _counter = walk_counter
        def visit_FunctionDef(self, node: ast.FunctionDef):
            self.generic_visit(node)
            nonlocal straight_line_count, if_chain_count
            body = node.body
            docstring = None
            work_body = body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstring = body[0]
                work_body = body[1:]
            if not work_body:
                return node
            if len(work_body) == 1 and isinstance(work_body[0], ast.If):
                branches = _collect_if_chain(work_body[0])
                if branches is not None:
                    _brng = _seeded_rng(
                        build_seed, f"wyrm_branch::{node.name}::{_Walker._counter[0]}")
                    _Walker._counter[0] += 1
                    new_stmts = _build_branch_dispatcher(
                        branches, branch_name, rng=_brng)
                    node.body = ([docstring] if docstring else []) + new_stmts
                    if_chain_count += 1
                    return node
            if _is_straight_line_block(work_body):
                rng = _seeded_rng(build_seed, f"wyrm::{node.name}::{_Walker._counter[0]}")
                _Walker._counter[0] += 1
                new_stmts = _build_straight_line_dispatcher(work_body, rng, state_var=state_name)
                node.body = ([docstring] if docstring else []) + new_stmts
                straight_line_count += 1
                return node
            if len(work_body) >= 3:
                _pref: List[ast.stmt] = []
                for _st in work_body:
                    if (isinstance(_st, SIMPLE_STMT_TYPES)
                            and not isinstance(_st, ast.Pass)
                            and not _contains_suspend_or_jump(_st)):
                        _pref.append(_st)
                    else:
                        break
                if len(_pref) >= 2 and _is_straight_line_block(_pref):
                    _suffix = work_body[len(_pref):]
                    rng = _seeded_rng(
                        build_seed, f"wyrm_prefix::{node.name}::{_Walker._counter[0]}")
                    _Walker._counter[0] += 1
                    new_stmts = _build_straight_line_dispatcher(
                        _pref, rng, state_var=state_name)
                    node.body = (([docstring] if docstring else [])
                                 + new_stmts + _suffix)
                    straight_line_count += 1
                    return node
            return node
        def visit_AsyncFunctionDef(self, node):
            return node
    _Walker().visit(tree)
    ast.fix_missing_locations(tree)
    return straight_line_count, if_chain_count
def cfg_metrics(tree: ast.Module) -> Dict[str, int]:
    m = {"if": 0, "loop": 0, "try": 0, "boolop": 0, "functions": 0}
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            m["if"] += 1
        elif isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
            m["loop"] += 1
        elif isinstance(node, ast.Try):
            m["try"] += 1
        elif isinstance(node, ast.BoolOp):
            m["boolop"] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            m["functions"] += 1
    return m
class WhileFormDiversification(_SkipGeneratedTransformer):
    def __init__(self, build_seed: int, coverage: float = 0.5):
        self.rng = _seeded_rng(build_seed, "while_diversify")
        self.coverage = min(1.0, max(0.05, coverage))
        self.converted = 0
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        outer = self
        class _V(_SkipGeneratedTransformer):
            def visit_While(inner_self, node: ast.While):
                inner_self.generic_visit(node)
                if node.orelse or is_generated(node.test):
                    return node
                if outer.rng.random() <= outer.coverage:
                    guard_test = ast.UnaryOp(op=ast.Not(), operand=node.test)
                    mark_generated(guard_test)
                    guard = ast.If(test=guard_test,
                                   body=[ast.Break()], orelse=[])
                    ast.copy_location(guard, node)
                    ast.fix_missing_locations(guard)
                    mark_generated(guard)
                    node.test = ast.Constant(value=True)
                    node.body.insert(0, guard)
                    outer.converted += 1
                return node
        self.converted = 0
        _V().visit(tree)
        return self.converted
class BranchNormalizationPass(_SkipGeneratedTransformer):
    def __init__(self, build_seed: int, coverage: float = 0.4):
        self.rng = _seeded_rng(build_seed, "branch_normalize")
        self.coverage = min(1.0, max(0.05, coverage))
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        count = [0]
        outer = self
        class _V(_SkipGeneratedTransformer):
            def visit_If(inner_self, node: ast.If):
                inner_self.generic_visit(node)
                if (len(node.body) == 1 and len(node.orelse) == 1
                        and not is_generated(node)
                        and isinstance(node.body[0], ast.Assign)
                        and isinstance(node.orelse[0], ast.Assign)
                        and len(node.body[0].targets) == 1
                        and len(node.orelse[0].targets) == 1
                        and isinstance(node.body[0].targets[0], ast.Name)
                        and isinstance(node.orelse[0].targets[0], ast.Name)
                        and node.body[0].targets[0].id == node.orelse[0].targets[0].id
                        and isinstance(node.body[0].targets[0].ctx, ast.Store)):
                    t_val, f_val = node.body[0].value, node.orelse[0].value
                    unsafe = any(
                        isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await,
                                       ast.NamedExpr))
                        for n in list(ast.walk(t_val)) + list(ast.walk(f_val)))
                    if not unsafe and outer.rng.random() <= outer.coverage:
                        target_id = node.body[0].targets[0].id
                        iexp = ast.IfExp(test=node.test,
                                         body=t_val, orelse=f_val)
                        new_assign = ast.Assign(
                            targets=[ast.Name(id=target_id, ctx=ast.Store())],
                            value=iexp)
                        ast.copy_location(new_assign, node)
                        ast.fix_missing_locations(new_assign)
                        count[0] += 1
                        return new_assign
                return node
        _V().visit(tree)
        return count[0]
class ForToWhilePass(_SkipGeneratedTransformer):
    name = "for_to_while"
    def __init__(self, build_seed: int, coverage: float = 0.5):
        self.rng = _seeded_rng(build_seed, "for_to_while")
        self.coverage = min(1.0, max(0.05, coverage))
        self.converted = 0
    @staticmethod
    def _unsuitable_body(body: List[ast.stmt]) -> bool:
        for n in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(n, (ast.AsyncFor,
                              ast.AsyncWith, ast.Yield, ast.YieldFrom,
                              ast.Await)):
                return True
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "next"):
                return True
        return False
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        self.converted = 0
        taken = {n.id for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id}
        bound: Set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                bound.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)):
                bound.add(n.name)
            elif isinstance(n, ast.arg):
                bound.add(n.arg)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, ast.alias) and n.asname:
                bound.add(n.asname)
        outer = self
        def _fresh(prefix: str) -> str:
            cand = f"{prefix}{outer.rng.randrange(16**6):06x}"
            while cand in taken:
                cand = f"{prefix}{outer.rng.randrange(16**6):06x}"
            taken.add(cand)
            return cand
        class _V(_SkipGeneratedTransformer):
            def visit_For(inner_self, node: ast.For):
                inner_self.generic_visit(node)
                if (is_generated(node)
                        or not isinstance(node.target, (ast.Name, ast.Tuple, ast.List))
                        or outer._unsuitable_body(node.body)
                        or "iter" in bound or "next" in bound):
                    return node
                if outer.rng.random() > outer.coverage:
                    return node
                it_name = _fresh("_fwit")
                mark_generated(node.iter)
                iter_call = ast.Call(func=ast.Name(id="iter", ctx=ast.Load()),
                                     args=[node.iter], keywords=[])
                iter_assign = ast.Assign(
                    targets=[ast.Name(id=it_name, ctx=ast.Store())],
                    value=iter_call)
                ast.copy_location(iter_assign, node)
                mark_generated(iter_assign)
                if isinstance(node.target, ast.Name):
                    fetch = ast.Assign(
                        targets=[ast.Name(id=node.target.id, ctx=ast.Store())],
                        value=ast.Call(
                            func=ast.Name(id="next", ctx=ast.Load()),
                            args=[ast.Name(id=it_name, ctx=ast.Load())],
                            keywords=[]))
                    ast.copy_location(fetch, node)
                    mark_generated(fetch)
                    fetch_stmts = [fetch]
                else:
                    tmp_name = _fresh("_fwt")
                    fetch_tmp = ast.Assign(
                        targets=[ast.Name(id=tmp_name, ctx=ast.Store())],
                        value=ast.Call(
                            func=ast.Name(id="next", ctx=ast.Load()),
                            args=[ast.Name(id=it_name, ctx=ast.Load())],
                            keywords=[]))
                    ast.copy_location(fetch_tmp, node)
                    mark_generated(fetch_tmp)
                    unpack = ast.Assign(
                        targets=[copy.deepcopy(node.target)],
                        value=ast.Name(id=tmp_name, ctx=ast.Load()))
                    ast.copy_location(unpack, node)
                    mark_generated(unpack)
                    fetch_stmts = [fetch_tmp, unpack]
                handler = ast.ExceptHandler(
                    type=ast.Name(id="StopIteration", ctx=ast.Load()),
                    name=None, body=[ast.Break()])
                ast.copy_location(handler, node)
                mark_generated(handler)
                guard = ast.Try(body=fetch_stmts, handlers=[handler],
                                orelse=[], finalbody=[])
                ast.copy_location(guard, node)
                ast.fix_missing_locations(guard)
                mark_generated(guard)
                loop = ast.While(test=_loop_test(outer.rng),
                                 body=[guard] + node.body, orelse=[])
                ast.copy_location(loop, node)
                mark_generated(loop)
                if node.orelse:
                    exh_name = _fresh("_fwexh")
                    exh_assign = ast.Assign(
                        targets=[ast.Name(id=exh_name, ctx=ast.Store())],
                        value=ast.Constant(value=False))
                    ast.copy_location(exh_assign, node)
                    mark_generated(exh_assign)
                    set_true = ast.Assign(
                        targets=[ast.Name(id=exh_name, ctx=ast.Store())],
                        value=ast.Constant(value=True))
                    ast.copy_location(set_true, node)
                    mark_generated(set_true)
                    handler.body.insert(0, set_true)
                    run_else = ast.If(
                        test=ast.Name(id=exh_name, ctx=ast.Load()),
                        body=list(node.orelse), orelse=[])
                    ast.copy_location(run_else, node)
                    ast.fix_missing_locations(run_else)
                    mark_generated(run_else)
                    outer.converted += 1
                    return [iter_assign, exh_assign, loop, run_else]
                outer.converted += 1
                return [iter_assign, loop]
        _V().visit(tree)
        ast.fix_missing_locations(tree)
        return self.converted
class ModuleEntryWrapPass:
    name = "module_entry_wrap"
    _ALLOWED_TOP = (
        ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.If,
        ast.While, ast.For, ast.AsyncFor, ast.Try, ast.With, ast.AsyncWith,
        ast.Raise, ast.Assert, ast.Delete, ast.Pass, ast.Import,
        ast.ImportFrom, ast.Match, ast.FunctionDef, ast.AsyncFunctionDef,
        ast.ClassDef,
    )
    def __init__(self, naming: "RuneNaming", max_input_nodes: int = 400,
                 script_mode: bool = False):
        self.naming = naming
        self.max_input_nodes = max_input_nodes
        self.script_mode = script_mode
    def _eligible(self, tree: ast.Module) -> bool:
        if not tree.body:
            return False
        total = sum(1 for _ in ast.walk(tree))
        if total > self.max_input_nodes:
            return False
        for stmt in tree.body:
            if isinstance(stmt, (ast.Global, ast.Nonlocal)):
                return False
            if not isinstance(stmt, self._ALLOWED_TOP):
                return False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and \
                    any(a.name == "*" for a in node.names):
                return False
        return True
    @staticmethod
    def _scan_walrus(node: ast.AST, out: Set[str]) -> None:
        for n in ast.walk(node):
            if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
    @classmethod
    def _collect_module_names(cls, body: List[ast.stmt]) -> Set[str]:
        out: Set[str] = set()
        def rec(node: ast.AST):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(node.name)
                for d in node.decorator_list:
                    cls._scan_walrus(d, out)
                for d in node.args.defaults:
                    cls._scan_walrus(d, out)
                for d in node.args.kw_defaults:
                    if d is not None:
                        cls._scan_walrus(d, out)
                return
            if isinstance(node, ast.Lambda):
                for d in node.args.defaults:
                    rec(d)
                for d in node.args.kw_defaults:
                    if d is not None:
                        rec(d)
                return
            if isinstance(node, ast.ClassDef):
                out.add(node.name)
                return
            if isinstance(node, (ast.ListComp, ast.SetComp,
                                 ast.DictComp, ast.GeneratorExp)):
                cls._scan_walrus(node, out)
                return
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                out.add(node.id)
                return
            if isinstance(node, ast.Import):
                for a in node.names:
                    out.add(a.asname or a.name.split(".")[0])
                return
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    b = a.asname or a.name
                    if b != "*":
                        out.add(b)
                return
            if isinstance(node, ast.ExceptHandler) and node.name:
                out.add(node.name)
            if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                out.add(node.name)
            for ch in ast.iter_child_nodes(node):
                rec(ch)
        for stmt in body:
            rec(stmt)
        return out
    def transform(self, tree: ast.Module, rng: "random.Random") -> int:
        if not self._eligible(tree):
            return 0
        module_names = sorted(self._collect_module_names(tree.body))
        fn_name = self.naming.generate("entry_wrap::main")
        body: List[ast.stmt] = list(tree.body)
        if module_names and not self.script_mode:
            body.insert(0, ast.Global(names=module_names))
        fn_def = ast.FunctionDef(
            name=fn_name,
            args=ast.arguments(posonlyargs=[], args=[], vararg=None,
                               kwonlyargs=[], kw_defaults=[], kwarg=None,
                               defaults=[]),
            body=body,
            decorator_list=[],
            returns=None,
            type_comment=None,
        )
        ast.fix_missing_locations(fn_def)
        call_stmt = ast.Expr(value=ast.Call(
            func=ast.Name(id=fn_name, ctx=ast.Load()),
            args=[], keywords=[]))
        ast.fix_missing_locations(call_stmt)
        mark_generated(call_stmt)
        tree.body = [fn_def, call_stmt]
        return 1
class IRKind(Enum):
    CONST = auto()
    LOAD = auto()
    LOAD_GLOBAL = auto()
    STORE = auto()
    BINOP = auto()
    UNARYOP = auto()
    COMPARE = auto()
    BOOL_AND_TEST = auto()
    BOOL_OR_TEST = auto()
    POP = auto()
    BUILD_LIST = auto()
    BUILD_TUPLE = auto()
    BUILD_MAP = auto()
    BUILD_SET = auto()
    SUBSCRIPT = auto()
    STORE_SUBSCR = auto()
    CALL_FUNC = auto()
    BUILD_SLICE = auto()
    CONVERT = auto()
    BUILD_STR = auto()
    FORMAT_SPEC = auto()
    GETATTR = auto()
    CALL_BUILTIN = auto()
    CALL_PRINT = auto()
    JUMP = auto()
    JUMP_IF_FALSE = auto()
    JUMP_IF_FALSE_OR_POP = auto()
    LABEL = auto()
    RETURN = auto()
    NOP = auto()
    STORE_ATTR = auto()
    STORE_GLOBAL = auto()
    UNPACK_SEQUENCE = auto()
    RAISE = auto()
    DELETE_NAME = auto()
    DELETE_ATTR = auto()
    DELETE_SUBSCR = auto()
    DELETE_GLOBAL = auto()
    IMPORT_NAME = auto()
    GET_ITER = auto()
    FOR_ITER = auto()
    LIST_APPEND = auto()
    SET_ADD = auto()
    MAP_ADD = auto()
    CALL_FUNC_KW = auto()
    SETUP_FIN = auto()
    POP_FIN = auto()
    EXC_MATCH = auto()
    DUP_TOP = auto()
    CONST_STORE = auto()
@dataclass
class IRInstruction:
    kind: IRKind
    a: Any = None
    b: Any = None
    target: Any = None
    def __repr__(self):
        parts = [self.kind.name]
        if self.a is not None:
            parts.append(f"a={self.a!r}")
        if self.b is not None:
            parts.append(f"b={self.b!r}")
        if self.target is not None:
            parts.append(f"target={self.target!r}")
        return " ".join(parts)
@dataclass
class IRProgram:
    instructions: List[IRInstruction] = field(default_factory=list)
    def emit(self, kind: IRKind, a=None, b=None, target=None):
        self.instructions.append(IRInstruction(kind, a, b, target))
        return self.instructions[-1]
class IRBuildError(Exception):
    pass
class ASTToIR:
    _BINOP_MAP = {
        ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
        ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
        ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
        ast.LShift: "<<", ast.RShift: ">>",
    }
    _CMP_MAP = {
        ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<",
        ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
        ast.In: "in", ast.NotIn: "not in",
        ast.Is: "is", ast.IsNot: "is not",
    }
    _UNARY_MAP = {
        ast.Not: "not", ast.USub: "-", ast.UAdd: "+", ast.Invert: "~",
    }
    def __init__(self):
        self._label_counter = 0
        self._loop_stack: List[Tuple[str, str, int]] = []
        self._tmp_counter = 0
        self._arg_names: Set[str] = set()
        self._store_names: Set[str] = set()
        self._global_names: Set[str] = set()
        self._try_depth = 0
        self._eager_builtins = frozenset({
            "sum", "min", "max", "list", "tuple", "set", "frozenset",
            "sorted", "dict",
        })
    def _new_label(self, hint: str) -> str:
        self._label_counter += 1
        return f"L{self._label_counter}_{hint}"
    def lower_function(self, func: ast.FunctionDef) -> IRProgram:
        prog = IRProgram()
        self._arg_names = ({a.arg for a in func.args.posonlyargs}
                           | {a.arg for a in func.args.args}
                           | {a.arg for a in func.args.kwonlyargs})
        if func.args.vararg:
            self._arg_names.add(func.args.vararg.arg)
        if func.args.kwarg:
            self._arg_names.add(func.args.kwarg.arg)
        self._store_names = {
            n.id for n in ast.walk(func)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        for _n in ast.walk(func):
            if isinstance(_n, ast.Import):
                for _a in _n.names:
                    self._store_names.add(_a.asname or _a.name.split(".")[0])
            elif isinstance(_n, ast.ImportFrom):
                for _a in _n.names:
                    if _a.asname:
                        self._store_names.add(_a.asname)
                    elif _a.name != "*":
                        self._store_names.add(_a.name)
            elif isinstance(_n, ast.ExceptHandler) and _n.name:
                self._store_names.add(_n.name)
        self._global_names = {
            name for n in ast.walk(func) if isinstance(n, ast.Global)
            for name in n.names}
        if any(isinstance(n, ast.Nonlocal) for n in ast.walk(func)):
            raise IRBuildError("nonlocal bindings are not supported "
                               f"at line {self._lineno(func)}")
        self._store_names -= self._global_names
        self._try_depth = 0
        self._rename_comp_vars(func)
        self._rewrite_eager_genexps(func)
        self._lower_body(func.body, prog)
        return prog
    def _emit_store(self, name: str, prog: IRProgram) -> None:
        if name in self._global_names:
            prog.emit(IRKind.STORE_GLOBAL, name)
        else:
            prog.emit(IRKind.STORE, target=name)
    def _emit_load(self, name: str, prog: IRProgram) -> None:
        if name in self._global_names:
            prog.emit(IRKind.LOAD_GLOBAL, a=name)
        else:
            prog.emit(IRKind.LOAD, a=name)
    def _pop_trys(self, prog: IRProgram, depth: int) -> None:
        while self._try_depth > depth:
            prog.emit(IRKind.POP_FIN)
            self._try_depth -= 1
    def _lower_store_stack(self, tgt: ast.expr, prog: IRProgram) -> None:
        if isinstance(tgt, ast.Name):
            self._emit_store(tgt.id, prog)
        elif isinstance(tgt, ast.Attribute):
            prog.emit(IRKind.STORE, target="#stv0")
            self._lower_expr(tgt.value, prog)
            prog.emit(IRKind.LOAD, a="#stv0")
            prog.emit(IRKind.STORE_ATTR, tgt.attr)
        elif isinstance(tgt, ast.Subscript):
            prog.emit(IRKind.STORE, target="#stv0")
            self._lower_expr(tgt.value, prog)
            self._lower_slice(tgt.slice, prog)
            prog.emit(IRKind.LOAD, a="#stv0")
            prog.emit(IRKind.STORE_SUBSCR)
        elif isinstance(tgt, (ast.Tuple, ast.List)):
            elts = tgt.elts
            if any(isinstance(e, ast.Starred) for e in elts):
                raise IRBuildError("starred assignment targets are not supported")
            prog.emit(IRKind.UNPACK_SEQUENCE, len(elts))
            for e in elts:
                self._lower_store_stack(e, prog)
        else:
            raise IRBuildError("unsupported assignment target")
    def _lower_store_target(self, tgt: ast.expr, prog: IRProgram) -> None:
        self._lower_store_stack(tgt, prog)
    def _lower_single_assign(self, tgt: ast.expr, value: ast.expr,
                             prog: IRProgram) -> None:
        if isinstance(tgt, ast.Attribute):
            self._lower_expr(tgt.value, prog)
            self._lower_expr(value, prog)
            prog.emit(IRKind.STORE_ATTR, tgt.attr)
        elif isinstance(tgt, ast.Subscript):
            self._lower_expr(tgt.value, prog)
            self._lower_slice(tgt.slice, prog)
            self._lower_expr(value, prog)
            prog.emit(IRKind.STORE_SUBSCR)
        elif isinstance(tgt, (ast.Tuple, ast.List)):
            stars = [i for i, e in enumerate(tgt.elts)
                     if isinstance(e, ast.Starred)]
            if any(isinstance(e, (ast.Tuple, ast.List)) and any(
                    isinstance(x, ast.Starred) for x in ast.walk(e))
                    for e in tgt.elts):
                raise IRBuildError("nested starred assignment targets are not supported")
            if len(stars) > 1:
                raise IRBuildError("multiple starred assignment targets are not supported")
            if stars:
                self._lower_starred_assign(tgt, value, stars[0], prog)
                return
            self._lower_expr(value, prog)
            prog.emit(IRKind.UNPACK_SEQUENCE, len(tgt.elts))
            for e in tgt.elts:
                self._lower_store_stack(e, prog)
        elif isinstance(tgt, ast.Name):
            self._lower_expr(value, prog)
            self._emit_store(tgt.id, prog)
        else:
            raise IRBuildError("unsupported assignment target")
    def _lower_starred_assign(self, tgt: Union[ast.Tuple, ast.List],
                                value: ast.expr, star_idx: int,
                                prog: IRProgram) -> None:
        pre = tgt.elts[:star_idx]
        star = tgt.elts[star_idx]
        assert isinstance(star, ast.Starred)
        post = tgt.elts[star_idx + 1:]
        assert isinstance(star.value, (ast.Name, ast.Attribute, ast.Subscript,
                                       ast.Tuple, ast.List))
        tmp = f"#star{self._tmp_counter}"
        self._tmp_counter += 1
        self._lower_expr(value, prog)
        prog.emit(IRKind.CALL_BUILTIN, a="list", b=1)
        prog.emit(IRKind.STORE, target=tmp)
        need = len(pre) + len(post)
        fail_label = self._new_label("starfail")
        end_label = self._new_label("starend")
        prog.emit(IRKind.LOAD, a=tmp)
        prog.emit(IRKind.CALL_BUILTIN, a="len", b=1)
        prog.emit(IRKind.CONST, a=need)
        prog.emit(IRKind.COMPARE, a=">=")
        prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
        for i, e in enumerate(pre):
            prog.emit(IRKind.LOAD, a=tmp)
            prog.emit(IRKind.CONST, a=i)
            prog.emit(IRKind.SUBSCRIPT)
            self._lower_store_stack(e, prog)
        prog.emit(IRKind.LOAD, a=tmp)
        prog.emit(IRKind.CONST, a=len(pre))
        if post:
            prog.emit(IRKind.LOAD, a=tmp)
            prog.emit(IRKind.CALL_BUILTIN, a="len", b=1)
            prog.emit(IRKind.CONST, a=len(post))
            prog.emit(IRKind.BINOP, a="-")
        else:
            prog.emit(IRKind.CONST, a=None)
        prog.emit(IRKind.CONST, a=None)
        prog.emit(IRKind.BUILD_SLICE)
        prog.emit(IRKind.SUBSCRIPT)
        self._lower_store_stack(star.value, prog)
        for j, e in enumerate(post):
            prog.emit(IRKind.LOAD, a=tmp)
            prog.emit(IRKind.LOAD, a=tmp)
            prog.emit(IRKind.CALL_BUILTIN, a="len", b=1)
            prog.emit(IRKind.CONST, a=len(post) - j)
            prog.emit(IRKind.BINOP, a="-")
            prog.emit(IRKind.SUBSCRIPT)
            self._lower_store_stack(e, prog)
        prog.emit(IRKind.JUMP, target=end_label)
        prog.emit(IRKind.LABEL, target=fail_label)
        self._lower_expr(ast.Call(
            func=ast.Name(id="ValueError", ctx=ast.Load()),
            args=[ast.Constant(value="not enough values to unpack")],
            keywords=[]), prog)
        prog.emit(IRKind.RAISE)
        prog.emit(IRKind.LABEL, target=end_label)
    def _lower_aug_assign(self, stmt: ast.AugAssign, prog: IRProgram) -> None:
        op_tag = self._BINOP_MAP.get(type(stmt.op))
        if op_tag is None:
            raise IRBuildError(f"Unsupported augmented-assignment operator "
                               f"{type(stmt.op).__name__} at line {self._lineno(stmt)}")
        tgt = stmt.target
        if isinstance(tgt, ast.Name):
            prog.emit(IRKind.LOAD, a=tgt.id) if tgt.id not in self._global_names \
                else prog.emit(IRKind.LOAD_GLOBAL, a=tgt.id)
            self._lower_expr(stmt.value, prog)
            prog.emit(IRKind.BINOP, a=op_tag)
            self._emit_store(tgt.id, prog)
        elif isinstance(tgt, (ast.Subscript, ast.Attribute)):
            tmp_o = f"#aug_o{self._tmp_counter}"
            tmp_k = f"#aug_k{self._tmp_counter}"
            tmp_v = f"#aug_v{self._tmp_counter}"
            self._tmp_counter += 1
            if isinstance(tgt, ast.Subscript):
                self._lower_expr(tgt.value, prog)
                prog.emit(IRKind.STORE, target=tmp_o)
                self._lower_slice(tgt.slice, prog)
                prog.emit(IRKind.STORE, target=tmp_k)
                prog.emit(IRKind.LOAD, a=tmp_o)
                prog.emit(IRKind.LOAD, a=tmp_k)
                prog.emit(IRKind.SUBSCRIPT)
            else:
                self._lower_expr(tgt.value, prog)
                prog.emit(IRKind.STORE, target=tmp_o)
                prog.emit(IRKind.LOAD, a=tmp_o)
                prog.emit(IRKind.CONST, a=tgt.attr)
                prog.emit(IRKind.GETATTR)
            self._lower_expr(stmt.value, prog)
            prog.emit(IRKind.BINOP, a=op_tag)
            prog.emit(IRKind.STORE, target=tmp_v)
            prog.emit(IRKind.LOAD, a=tmp_o)
            if isinstance(tgt, ast.Subscript):
                prog.emit(IRKind.LOAD, a=tmp_k)
            prog.emit(IRKind.LOAD, a=tmp_v)
            if isinstance(tgt, ast.Subscript):
                prog.emit(IRKind.STORE_SUBSCR)
            else:
                prog.emit(IRKind.STORE_ATTR, tgt.attr)
        else:
            raise IRBuildError("unsupported augmented-assignment target "
                               f"at line {self._lineno(stmt)}")
    def _lower_slice(self, node: ast.expr, prog: IRProgram) -> None:
        if isinstance(node, ast.Slice):
            for part in (node.lower, node.upper, node.step):
                if part is None:
                    prog.emit(IRKind.CONST, a=None)
                else:
                    self._lower_expr(part, prog)
            prog.emit(IRKind.BUILD_SLICE)
        else:
            self._lower_expr(node, prog)
    def _rename_comp_vars(self, func: ast.FunctionDef) -> None:
        made: Set[str] = set()
        def _rec(node, active):
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                 ast.GeneratorExp)):
                sub = dict(active)
                for gen in node.generators:
                    _rec(gen.iter, sub)
                    for n in ast.walk(gen.target):
                        if isinstance(n, ast.Name) and isinstance(
                                n.ctx, ast.Store):
                            self._tmp_counter += 1
                            sub[n.id] = f"#cc{self._tmp_counter}_{n.id}"
                            made.add(sub[n.id])
                    _rec(gen.target, sub)
                    for cond in gen.ifs:
                        _rec(cond, sub)
                if isinstance(node, ast.DictComp):
                    _rec(node.key, sub)
                    _rec(node.value, sub)
                else:
                    _rec(node.elt, sub)
                return
            if isinstance(node, ast.Lambda):
                for d in list(node.args.defaults) + [
                        x for x in node.args.kw_defaults if x is not None]:
                    _rec(d, active)
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                for d in list(node.decorator_list):
                    _rec(d, active)
                return
            if isinstance(node, ast.Name) and node.id in active:
                node.id = active[node.id]
                return
            for child in ast.iter_child_nodes(node):
                _rec(child, active)
        for _d in list(func.args.defaults) + [
                _x for _x in func.args.kw_defaults if _x is not None]:
            _rec(_d, {})
        for _st in func.body:
            _rec(_st, {})
        self._store_names |= made
    def _rewrite_eager_genexps(self, func: ast.FunctionDef) -> None:
        parents: Dict[int, ast.AST] = {}
        for _p in ast.walk(func):
            for _ch in ast.iter_child_nodes(_p):
                parents.setdefault(id(_ch), _p)
        for node in list(ast.walk(func)):
            if not isinstance(node, ast.GeneratorExp):
                continue
            p = parents.get(id(node))
            ok = False
            if isinstance(p, ast.Call) and isinstance(p.func, ast.Name) \
                    and p.func.id in self._eager_builtins:
                ok = True
            elif isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) \
                    and p.func.attr == "join":
                ok = True
            if not ok:
                raise IRBuildError("lazy GeneratorExp outside eager consumers "
                                   f"at line {self._lineno(node)}")
            node.__class__ = ast.ListComp
    def _lower_body(self, body: List[ast.stmt], prog: IRProgram):
        for stmt in body:
            self._lower_stmt(stmt, prog)
    def _lineno(self, node) -> Any:
        return getattr(node, "lineno", "?")
    def _lower_stmt(self, stmt: ast.stmt, prog: IRProgram):
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) == 1:
                self._lower_single_assign(stmt.targets[0], stmt.value, prog)
            else:
                self._lower_expr(stmt.value, prog)
                for _ in stmt.targets[:-1]:
                    prog.emit(IRKind.DUP_TOP)
                for tgt in stmt.targets:
                    self._lower_store_target(tgt, prog)
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is None:
                if stmt.annotation is not None:
                    self._lower_expr(stmt.annotation, prog)
                    prog.emit(IRKind.POP)
            else:
                if stmt.annotation is not None:
                    self._lower_expr(stmt.annotation, prog)
                    prog.emit(IRKind.POP)
                self._lower_single_assign(stmt.target, stmt.value, prog)
        elif isinstance(stmt, ast.AugAssign):
            self._lower_aug_assign(stmt, prog)
        elif isinstance(stmt, ast.Assert):
            fail_label = self._new_label("assertbad")
            ok_label = self._new_label("assertok")
            self._lower_expr(stmt.test, prog)
            prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
            prog.emit(IRKind.JUMP, target=ok_label)
            prog.emit(IRKind.LABEL, target=fail_label)
            prog.emit(IRKind.LOAD_GLOBAL, a="AssertionError")
            if stmt.msg is not None:
                self._lower_expr(stmt.msg, prog)
            else:
                prog.emit(IRKind.CONST, a="")
            prog.emit(IRKind.CALL_FUNC, a=1)
            prog.emit(IRKind.RAISE)
            prog.emit(IRKind.LABEL, target=ok_label)
        elif isinstance(stmt, ast.Raise):
            if stmt.exc is None or stmt.cause is not None:
                raise IRBuildError("bare raise / raise-from are not supported "
                                   f"at line {self._lineno(stmt)}")
            self._lower_expr(stmt.exc, prog)
            prog.emit(IRKind.RAISE)
        elif isinstance(stmt, ast.Delete):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    if tgt.id in self._global_names:
                        prog.emit(IRKind.DELETE_GLOBAL, tgt.id)
                    else:
                        prog.emit(IRKind.DELETE_NAME, tgt.id)
                elif isinstance(tgt, ast.Attribute):
                    self._lower_expr(tgt.value, prog)
                    self._lower_expr(ast.Constant(value=tgt.attr), prog)
                    prog.emit(IRKind.DELETE_ATTR)
                elif isinstance(tgt, ast.Subscript):
                    self._lower_expr(tgt.value, prog)
                    self._lower_slice(tgt.slice, prog)
                    prog.emit(IRKind.DELETE_SUBSCR)
                else:
                    raise IRBuildError("unsupported del target "
                                       f"at line {self._lineno(stmt)}")
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.asname and "." in alias.name:
                    prog.emit(IRKind.IMPORT_NAME, (alias.name, ("_",)))
                else:
                    prog.emit(IRKind.IMPORT_NAME, (alias.name, ()))
                bound = alias.asname or alias.name.split(".")[0]
                self._emit_store(bound, prog)
        elif isinstance(stmt, ast.Global):
            pass
        elif isinstance(stmt, ast.ImportFrom):
            if stmt.module is None or any(a.name == "*" for a in stmt.names):
                raise IRBuildError("relative/star imports are not supported "
                                   f"at line {self._lineno(stmt)}")
            prog.emit(IRKind.IMPORT_NAME,
                      (stmt.module, tuple(a.name for a in stmt.names)))
            mod_tmp = f"#imp{self._tmp_counter}"
            self._tmp_counter += 1
            prog.emit(IRKind.STORE, target=mod_tmp)
            for alias in stmt.names:
                prog.emit(IRKind.LOAD, a=mod_tmp)
                prog.emit(IRKind.CONST, a=alias.name)
                prog.emit(IRKind.GETATTR)
                self._emit_store(alias.asname or alias.name, prog)
        elif isinstance(stmt, ast.Try):
            self._lower_try(stmt, prog)
        elif isinstance(stmt, ast.Pass):
            prog.emit(IRKind.NOP)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            self._lower_call_stmt(stmt.value, prog)
        elif isinstance(stmt, ast.Expr):
            self._lower_expr(stmt.value, prog)
            prog.emit(IRKind.POP)
        elif isinstance(stmt, ast.If):
            else_label = self._new_label("else")
            end_label = self._new_label("endif")
            self._lower_expr(stmt.test, prog)
            prog.emit(IRKind.JUMP_IF_FALSE, target=else_label)
            self._lower_body(stmt.body, prog)
            prog.emit(IRKind.JUMP, target=end_label)
            prog.emit(IRKind.LABEL, target=else_label)
            if stmt.orelse:
                self._lower_body(stmt.orelse, prog)
            prog.emit(IRKind.LABEL, target=end_label)
        elif isinstance(stmt, ast.While):
            loop_start = self._new_label("wstart")
            loop_else = self._new_label("welse")
            loop_end = self._new_label("wend")
            prog.emit(IRKind.LABEL, target=loop_start)
            self._lower_expr(stmt.test, prog)
            prog.emit(IRKind.JUMP_IF_FALSE, target=loop_else)
            self._loop_stack.append((loop_end, loop_start, self._try_depth))
            self._lower_body(stmt.body, prog)
            self._loop_stack.pop()
            prog.emit(IRKind.JUMP, target=loop_start)
            prog.emit(IRKind.LABEL, target=loop_else)
            if stmt.orelse:
                self._lower_body(stmt.orelse, prog)
            prog.emit(IRKind.LABEL, target=loop_end)
        elif isinstance(stmt, ast.For):
            it = stmt.iter
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) \
                    and it.func.id == "range" and not it.keywords:
                self._lower_for_range(stmt, prog)
            else:
                self._lower_for_general(stmt, prog)
        elif isinstance(stmt, ast.Break):
            if not self._loop_stack:
                raise IRBuildError(f"'break' outside loop at line {self._lineno(stmt)}")
            break_label, _, _depth = self._loop_stack[-1]
            self._pop_trys(prog, _depth)
            prog.emit(IRKind.JUMP, target=break_label)
        elif isinstance(stmt, ast.Continue):
            if not self._loop_stack:
                raise IRBuildError(f"'continue' outside loop at line {self._lineno(stmt)}")
            _, continue_label, _depth = self._loop_stack[-1]
            self._pop_trys(prog, _depth)
            prog.emit(IRKind.JUMP, target=continue_label)
        elif isinstance(stmt, ast.Return):
            if stmt.value is not None:
                self._lower_expr(stmt.value, prog)
            else:
                prog.emit(IRKind.CONST, a=None)
            self._pop_trys(prog, 0)
            prog.emit(IRKind.RETURN)
        elif isinstance(stmt, ast.Match):
            self._lower_match(stmt, prog)
        else:
            raise IRBuildError(f"Unsupported statement type: {type(stmt).__name__} at line {self._lineno(stmt)}")
    def _check_match_pattern(self, pat: ast.pattern) -> None:
        if isinstance(pat, (ast.MatchValue, ast.MatchSingleton)):
            return
        if isinstance(pat, ast.MatchAs):
            if pat.pattern is not None:
                self._check_match_pattern(pat.pattern)
            return
        if isinstance(pat, ast.MatchSequence):
            _stars = sum(1 for sub in pat.patterns
                         if isinstance(sub, ast.MatchStar))
            if _stars > 1:
                raise IRBuildError(
                    "multiple starred sub-patterns are not supported "
                    f"at line {self._lineno(pat)}")
            for sub in pat.patterns:
                if isinstance(sub, ast.MatchStar):
                    if sub.name is not None and not isinstance(sub.name, str):
                        raise IRBuildError(
                            "unsupported starred sub-pattern "
                            f"at line {self._lineno(pat)}")
                    continue
                self._check_match_pattern(sub)
            return
        raise IRBuildError(
            f"unsupported match pattern: {type(pat).__name__} "
            "(only value/singleton/as/fixed-sequence/starred-sequence lower to VM)")
    def _lower_match_pattern(self, pat: ast.pattern, subj: str,
                             prog: IRProgram, fail_label: str) -> None:
        if isinstance(pat, ast.MatchAs):
            if pat.pattern is not None:
                self._lower_match_pattern(pat.pattern, subj, prog, fail_label)
            if pat.name:
                if pat.name not in self._global_names:
                    self._store_names.add(pat.name)
                self._emit_load(subj, prog)
                self._emit_store(pat.name, prog)
            return
        if isinstance(pat, ast.MatchValue):
            self._emit_load(subj, prog)
            self._lower_expr(pat.value, prog)
            prog.emit(IRKind.COMPARE, a="==")
            prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
            return
        if isinstance(pat, ast.MatchSingleton):
            self._emit_load(subj, prog)
            prog.emit(IRKind.CONST, a=pat.value)
            prog.emit(IRKind.COMPARE, a="is")
            prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
            return
        if isinstance(pat, ast.MatchSequence):
            _star_idx = next(
                (i for i, sub in enumerate(pat.patterns)
                 if isinstance(sub, ast.MatchStar)), None)
            if _star_idx is None:
                n = len(pat.patterns)
                isinst = ast.Call(
                    func=ast.Name(id="isinstance", ctx=ast.Load()),
                    args=[ast.Name(id=subj, ctx=ast.Load()),
                          ast.Tuple(elts=[ast.Name(id="list", ctx=ast.Load()),
                                          ast.Name(id="tuple", ctx=ast.Load())],
                                    ctx=ast.Load())],
                    keywords=[])
                self._lower_expr(isinst, prog)
                prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
                lencall = ast.Call(
                    func=ast.Name(id="len", ctx=ast.Load()),
                    args=[ast.Name(id=subj, ctx=ast.Load())],
                    keywords=[])
                self._lower_expr(lencall, prog)
                prog.emit(IRKind.CONST, a=n)
                prog.emit(IRKind.COMPARE, a="==")
                prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
                for i, sub in enumerate(pat.patterns):
                    self._tmp_counter += 1
                    etmp = f"#matche{self._tmp_counter}"
                    self._store_names.add(etmp)
                    self._emit_load(subj, prog)
                    prog.emit(IRKind.CONST, a=i)
                    prog.emit(IRKind.SUBSCRIPT)
                    self._emit_store(etmp, prog)
                    self._lower_match_pattern(sub, etmp, prog, fail_label)
                return
            _fixed = len(pat.patterns) - 1
            _trail = len(pat.patterns) - 1 - _star_idx
            _isinst = ast.Call(
                func=ast.Name(id="isinstance", ctx=ast.Load()),
                args=[ast.Name(id=subj, ctx=ast.Load()),
                      ast.Tuple(elts=[ast.Name(id="list", ctx=ast.Load()),
                                      ast.Name(id="tuple", ctx=ast.Load())],
                                ctx=ast.Load())],
                keywords=[])
            self._lower_expr(_isinst, prog)
            prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
            _lencall = ast.Call(
                func=ast.Name(id="len", ctx=ast.Load()),
                args=[ast.Name(id=subj, ctx=ast.Load())],
                keywords=[])
            self._lower_expr(_lencall, prog)
            prog.emit(IRKind.CONST, a=_fixed)
            prog.emit(IRKind.COMPARE, a=">=")
            prog.emit(IRKind.JUMP_IF_FALSE, target=fail_label)
            for i, sub in enumerate(pat.patterns):
                if i == _star_idx:
                    _star = sub
                    if _star.name:
                        if _star.name not in self._global_names:
                            self._store_names.add(_star.name)
                        if _trail == 0:
                            _slice = ast.Slice(
                                lower=ast.Constant(value=_star_idx),
                                upper=None, step=None)
                        else:
                            _slice = ast.Slice(
                                lower=ast.Constant(value=_star_idx),
                                upper=ast.UnaryOp(
                                    op=ast.USub(),
                                    operand=ast.Constant(value=_trail)),
                                step=None)
                        _star_expr = ast.Call(
                            func=ast.Name(id="list", ctx=ast.Load()),
                            args=[ast.Subscript(
                                value=ast.Name(id=subj, ctx=ast.Load()),
                                slice=_slice, ctx=ast.Load())],
                            keywords=[])
                        self._lower_expr(_star_expr, prog)
                        self._emit_store(_star.name, prog)
                    continue
                self._tmp_counter += 1
                etmp = f"#matche{self._tmp_counter}"
                self._store_names.add(etmp)
                self._emit_load(subj, prog)
                if i < _star_idx:
                    prog.emit(IRKind.CONST, a=i)
                else:
                    prog.emit(IRKind.CONST, a=-(len(pat.patterns) - i))
                prog.emit(IRKind.SUBSCRIPT)
                self._emit_store(etmp, prog)
                self._lower_match_pattern(sub, etmp, prog, fail_label)
            return
        raise IRBuildError(
            f"unsupported match pattern: {type(pat).__name__}")
    def _lower_match(self, stmt: ast.Match, prog: IRProgram) -> None:
        for case in stmt.cases:
            self._check_match_pattern(case.pattern)
        self._tmp_counter += 1
        subj = f"#match{self._tmp_counter}"
        self._store_names.add(subj)
        self._lower_expr(stmt.subject, prog)
        self._emit_store(subj, prog)
        end_label = self._new_label("matchend")
        for case in stmt.cases:
            next_label = self._new_label("matchnext")
            self._lower_match_pattern(case.pattern, subj, prog, next_label)
            if case.guard is not None:
                self._lower_expr(case.guard, prog)
                prog.emit(IRKind.JUMP_IF_FALSE, target=next_label)
            self._lower_body(case.body, prog)
            prog.emit(IRKind.JUMP, target=end_label)
            prog.emit(IRKind.LABEL, target=next_label)
        prog.emit(IRKind.LABEL, target=end_label)
    @staticmethod
    def _try_body_abrupt(stmts: List[ast.stmt]) -> bool:
        stack: List[ast.AST] = list(stmts)
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda)):
                continue
            if isinstance(n, (ast.Return, ast.Break, ast.Continue)):
                return True
            stack.extend(ast.iter_child_nodes(n))
        return False
    def _lower_try_finally(self, stmt: ast.Try, prog: IRProgram) -> None:
        if self._try_body_abrupt(stmt.body):
            raise IRBuildError("try/finally with abrupt body exits is not supported "
                               f"at line {self._lineno(stmt)}")
        fin_label = self._new_label("fin")
        end_label = self._new_label("finend")
        prog.emit(IRKind.SETUP_FIN, target=fin_label)
        self._try_depth += 1
        _entry_depth = self._try_depth
        self._lower_body(stmt.body, prog)
        self._pop_trys(prog, _entry_depth - 1)
        self._lower_body(stmt.finalbody, prog)
        prog.emit(IRKind.JUMP, target=end_label)
        prog.emit(IRKind.LABEL, target=fin_label)
        self._lower_body(stmt.finalbody, prog)
        prog.emit(IRKind.RAISE)
        prog.emit(IRKind.LABEL, target=end_label)
    def _lower_try(self, stmt: ast.Try, prog: IRProgram) -> None:
        if stmt.finalbody and not stmt.orelse and not stmt.handlers:
            self._lower_try_finally(stmt, prog)
            return
        if stmt.orelse or stmt.finalbody:
            raise IRBuildError("try/else and try/finally are not supported "
                               f"at line {self._lineno(stmt)}")
        for h in stmt.handlers:
            if h.type is not None and not isinstance(
                    h.type, (ast.Name, ast.Attribute, ast.Tuple)):
                raise IRBuildError("unsupported except-handler type "
                                   f"at line {self._lineno(stmt)}")
            if isinstance(h.type, ast.Tuple) and (
                    not h.type.elts or any(
                        not isinstance(e, (ast.Name, ast.Attribute))
                        for e in h.type.elts)):
                raise IRBuildError("unsupported except-handler type "
                                   f"at line {self._lineno(stmt)}")
        end_label = self._new_label("tryend")
        handler_labels = [self._new_label(f"hdl{i}")
                          for i in range(len(stmt.handlers))]
        after_label = self._new_label("tryafter")
        prog.emit(IRKind.SETUP_FIN, target=handler_labels[0])
        self._try_depth += 1
        _entry_depth = self._try_depth
        self._lower_body(stmt.body, prog)
        self._pop_trys(prog, _entry_depth - 1)
        prog.emit(IRKind.JUMP, target=end_label)
        for i, (h, hlabel) in enumerate(zip(stmt.handlers, handler_labels)):
            prog.emit(IRKind.LABEL, target=hlabel)
            nxt = handler_labels[i + 1] if i + 1 < len(handler_labels) \
                else after_label
            if h.type is None:
                prog.emit(IRKind.POP)
            else:
                prog.emit(IRKind.DUP_TOP)
                if isinstance(h.type, ast.Tuple):
                    for e in h.type.elts:
                        self._lower_expr(e, prog)
                    prog.emit(IRKind.BUILD_TUPLE, len(h.type.elts))
                else:
                    self._lower_expr(h.type, prog)
                prog.emit(IRKind.EXC_MATCH)
                prog.emit(IRKind.JUMP_IF_FALSE, target=nxt)
                if h.name:
                    self._emit_store(h.name, prog)
                else:
                    prog.emit(IRKind.POP)
            self._lower_body(h.body, prog)
            if h.name:
                if h.name in self._global_names:
                    prog.emit(IRKind.DELETE_GLOBAL, h.name)
                else:
                    prog.emit(IRKind.DELETE_NAME, h.name)
            prog.emit(IRKind.JUMP, target=end_label)
        prog.emit(IRKind.LABEL, target=after_label)
        prog.emit(IRKind.RAISE)
        prog.emit(IRKind.LABEL, target=end_label)
    def _static_int_literal(self, node: ast.expr) -> Optional[int]:
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return node.value
        if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
                and isinstance(node.operand, ast.Constant)
                and isinstance(node.operand.value, int)
                and not isinstance(node.operand.value, bool)):
            return -node.operand.value
        return None
    def _lower_for_range(self, stmt: ast.For, prog: IRProgram):
        if not isinstance(stmt.target, ast.Name):
            raise IRBuildError(f"Unsupported for-loop target at line {self._lineno(stmt)} "
                                f"(only a plain name is supported)")
        it = stmt.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range"):
            raise IRBuildError(f"Unsupported for-loop iterable at line {self._lineno(stmt)} "
                                f"(only range(...) is supported)")
        nargs = len(it.args)
        if nargs == 1:
            start_expr, stop_expr, step_val = ast.Constant(value=0), it.args[0], 1
        elif nargs == 2:
            start_expr, stop_expr, step_val = it.args[0], it.args[1], 1
        elif nargs == 3:
            start_expr, stop_expr = it.args[0], it.args[1]
            step_node = it.args[2]
            step_val = self._static_int_literal(step_node)
            if step_val is None or step_val == 0:
                raise IRBuildError(f"range() step at line {self._lineno(stmt)} must be a nonzero "
                                    f"integer literal (direction must be known at compile time)")
        else:
            raise IRBuildError(f"Unsupported range() call at line {self._lineno(stmt)}")
        varname = stmt.target.id
        loop_start = self._new_label("fstart")
        loop_incr = self._new_label("fincr")
        loop_else = self._new_label("felse")
        loop_end = self._new_label("fend")
        self._lower_expr(start_expr, prog)
        self._emit_store(varname, prog)
        prog.emit(IRKind.LABEL, target=loop_start)
        self._emit_load(varname, prog)
        self._lower_expr(stop_expr, prog)
        prog.emit(IRKind.COMPARE, a="<" if step_val > 0 else ">")
        prog.emit(IRKind.JUMP_IF_FALSE, target=loop_else)
        self._loop_stack.append((loop_end, loop_incr, self._try_depth))
        self._lower_body(stmt.body, prog)
        self._loop_stack.pop()
        prog.emit(IRKind.LABEL, target=loop_incr)
        self._emit_load(varname, prog)
        prog.emit(IRKind.CONST, a=step_val)
        prog.emit(IRKind.BINOP, a="+")
        self._emit_store(varname, prog)
        prog.emit(IRKind.JUMP, target=loop_start)
        prog.emit(IRKind.LABEL, target=loop_else)
        if stmt.orelse:
            self._lower_body(stmt.orelse, prog)
        prog.emit(IRKind.LABEL, target=loop_end)
    def _lower_for_general(self, stmt: ast.For, prog: IRProgram) -> None:
        tgt = stmt.target
        if isinstance(tgt, (ast.Tuple, ast.List)) and any(
                isinstance(e, ast.Starred) for e in tgt.elts):
            raise IRBuildError("starred for-loop target is not supported "
                               f"at line {self._lineno(stmt)}")
        if not isinstance(tgt, (ast.Name, ast.Tuple, ast.List)):
            raise IRBuildError("unsupported for-loop target "
                               f"at line {self._lineno(stmt)}")
        loop_start = self._new_label("gstart")
        loop_else = self._new_label("gelse")
        loop_end = self._new_label("gend")
        self._lower_expr(stmt.iter, prog)
        prog.emit(IRKind.GET_ITER)
        prog.emit(IRKind.LABEL, target=loop_start)
        prog.emit(IRKind.FOR_ITER, target=loop_else)
        if isinstance(tgt, ast.Name):
            self._emit_store(tgt.id, prog)
        else:
            prog.emit(IRKind.UNPACK_SEQUENCE, len(tgt.elts))
            for e in tgt.elts:
                self._lower_store_target(e, prog)
        self._loop_stack.append((loop_end, loop_start, self._try_depth))
        self._lower_body(stmt.body, prog)
        self._loop_stack.pop()
        prog.emit(IRKind.JUMP, target=loop_start)
        prog.emit(IRKind.LABEL, target=loop_else)
        if stmt.orelse:
            self._lower_body(stmt.orelse, prog)
        prog.emit(IRKind.LABEL, target=loop_end)
    def _lower_comprehension(self, node, prog: IRProgram) -> None:
        self._tmp_counter += 1
        tag = self._tmp_counter
        acc = f"#cmp{tag}"
        if isinstance(node, ast.ListComp):
            prog.emit(IRKind.BUILD_LIST, 0)
        elif isinstance(node, ast.SetComp):
            prog.emit(IRKind.BUILD_SET, 0)
        else:
            prog.emit(IRKind.BUILD_MAP, 0)
        prog.emit(IRKind.STORE, target=acc)
        gens = node.generators
        end_labels: List[str] = []
        def _emit_gens(idx: int) -> None:
            if idx >= len(gens):
                if isinstance(node, ast.DictComp):
                    prog.emit(IRKind.LOAD, a=acc)
                    self._lower_expr(node.key, prog)
                    self._lower_expr(node.value, prog)
                    prog.emit(IRKind.MAP_ADD)
                    prog.emit(IRKind.POP)
                else:
                    prog.emit(IRKind.LOAD, a=acc)
                    self._lower_expr(node.elt, prog)
                    if isinstance(node, ast.ListComp):
                        prog.emit(IRKind.LIST_APPEND)
                    else:
                        prog.emit(IRKind.SET_ADD)
                    prog.emit(IRKind.POP)
                return
            gen = gens[idx]
            if gen.is_async:
                raise IRBuildError("async comprehensions are not supported")
            self._lower_expr(gen.iter, prog)
            prog.emit(IRKind.GET_ITER)
            ls = self._new_label("cstart")
            le = self._new_label("cend")
            end_labels.append(le)
            prog.emit(IRKind.LABEL, target=ls)
            prog.emit(IRKind.FOR_ITER, target=le)
            if isinstance(gen.target, ast.Name):
                self._emit_store(gen.target.id, prog)
            elif isinstance(gen.target, (ast.Tuple, ast.List)) and not any(
                    isinstance(e, ast.Starred) for e in gen.target.elts):
                prog.emit(IRKind.UNPACK_SEQUENCE, len(gen.target.elts))
                for e in gen.target.elts:
                    self._lower_store_target(e, prog)
            else:
                raise IRBuildError("unsupported comprehension target")
            skip_all = self._new_label("cskip") if gen.ifs else None
            for cond in gen.ifs:
                self._lower_expr(cond, prog)
                prog.emit(IRKind.JUMP_IF_FALSE, target=skip_all)
            _emit_gens(idx + 1)
            if skip_all is not None:
                prog.emit(IRKind.LABEL, target=skip_all)
            prog.emit(IRKind.JUMP, target=ls)
            prog.emit(IRKind.LABEL, target=le)
        _emit_gens(0)
        prog.emit(IRKind.LOAD, a=acc)
    def _lower_call_stmt(self, call: ast.Call, prog: IRProgram):
        if isinstance(call.func, ast.Name) and call.func.id == "print" \
                and not call.keywords:
            for a in call.args:
                self._lower_expr(a, prog)
            prog.emit(IRKind.CALL_PRINT, a=len(call.args))
            return
        self._lower_expr(call, prog)
        prog.emit(IRKind.POP)
    def _lower_formatted_value(self, part: ast.FormattedValue,
                               prog: IRProgram) -> None:
        conv = part.conversion
        conv_id = {-1: 0, ord("s"): 1,
                   ord("r"): 2, ord("a"): 3}.get(
                       -1 if conv is None else conv)
        if conv_id is None:
            raise IRBuildError(f"Unsupported f-string conversion "
                               f"at line {self._lineno(part)}")
        self._lower_expr(part.value, prog)
        if part.format_spec is None:
            prog.emit(IRKind.CONVERT, a=conv_id)
            return
        if conv_id != 0:
            prog.emit(IRKind.CONVERT, a=conv_id)
        if part.format_spec is not None:
            n_spec = 0
            for sp in part.format_spec.values:
                if isinstance(sp, ast.Constant):
                    if not isinstance(sp.value, str):
                        raise IRBuildError(
                            f"Unsupported f-string spec part at line {self._lineno(part)}")
                    prog.emit(IRKind.CONST, a=sp.value)
                elif isinstance(sp, ast.FormattedValue):
                    self._lower_formatted_value(sp, prog)
                else:
                    raise IRBuildError(f"Unsupported f-string spec part "
                                       f"at line {self._lineno(part)}")
                n_spec += 1
            prog.emit(IRKind.BUILD_STR, a=n_spec)
            prog.emit(IRKind.FORMAT_SPEC)
    def _lower_expr(self, expr: ast.expr, prog: IRProgram):
        if isinstance(expr, ast.Constant) and (
                expr.value is None or isinstance(expr.value, (int, float, str, bool))):
            prog.emit(IRKind.CONST, a=expr.value)
        elif isinstance(expr, ast.Name):
            if (isinstance(expr.ctx, ast.Load)
                    and expr.id not in self._arg_names
                    and expr.id not in self._store_names):
                prog.emit(IRKind.LOAD_GLOBAL, a=expr.id)
            else:
                prog.emit(IRKind.LOAD, a=expr.id)
        elif isinstance(expr, ast.BinOp):
            op_tag = self._BINOP_MAP.get(type(expr.op))
            if op_tag is None:
                raise IRBuildError(f"Unsupported binary operator: {type(expr.op).__name__} "
                                    f"at line {self._lineno(expr)}")
            self._lower_expr(expr.left, prog)
            self._lower_expr(expr.right, prog)
            prog.emit(IRKind.BINOP, a=op_tag)
        elif isinstance(expr, ast.UnaryOp):
            op_tag = self._UNARY_MAP.get(type(expr.op))
            if op_tag is None:
                raise IRBuildError(f"Unsupported unary operator: {type(expr.op).__name__} "
                                    f"at line {self._lineno(expr)}")
            self._lower_expr(expr.operand, prog)
            prog.emit(IRKind.UNARYOP, a=op_tag)
        elif isinstance(expr, ast.BoolOp):
            end_label = self._new_label("boolop")
            probe_kind = IRKind.BOOL_AND_TEST if isinstance(expr.op, ast.And) else IRKind.BOOL_OR_TEST
            for i, value in enumerate(expr.values):
                self._lower_expr(value, prog)
                if i < len(expr.values) - 1:
                    prog.emit(probe_kind, target=end_label)
            prog.emit(IRKind.LABEL, target=end_label)
        elif isinstance(expr, ast.Compare):
            if len(expr.ops) == 1:
                op_tag = self._CMP_MAP.get(type(expr.ops[0]))
                if op_tag is None:
                    raise IRBuildError(f"Unsupported comparison operator: "
                                        f"{type(expr.ops[0]).__name__} at line {self._lineno(expr)}")
                self._lower_expr(expr.left, prog)
                self._lower_expr(expr.comparators[0], prog)
                prog.emit(IRKind.COMPARE, a=op_tag)
            else:
                if any(type(op) not in self._CMP_MAP for op in expr.ops):
                    raise IRBuildError(f"Unsupported chained-comparison operator "
                                        f"at line {self._lineno(expr)}")
                self._tmp_counter += 1
                base = self._tmp_counter
                end_l = self._new_label("cmpchain")
                prev_t = f"#c{base}_0"
                self._lower_expr(expr.left, prog)
                prog.emit(IRKind.STORE, target=prev_t)
                n_links = len(expr.ops)
                for i in range(n_links):
                    cur_t = f"#c{base}_{i + 1}"
                    prog.emit(IRKind.LOAD, a=prev_t)
                    self._lower_expr(expr.comparators[i], prog)
                    prog.emit(IRKind.STORE, target=cur_t)
                    prog.emit(IRKind.COMPARE, a=self._CMP_MAP[type(expr.ops[i])])
                    if i < n_links - 1:
                        prog.emit(IRKind.JUMP_IF_FALSE_OR_POP, target=end_l)
                    prev_t = cur_t
                prog.emit(IRKind.LABEL, target=end_l)
                prog.emit(IRKind.CONVERT, a=4)
        elif isinstance(expr, ast.Call):
            if expr.keywords and any(kw.arg is None for kw in expr.keywords):
                raise IRBuildError(f"**-unpacking in calls is not supported "
                                    f"at line {self._lineno(expr)}")
            if expr.keywords:
                if isinstance(expr.func, ast.Name):
                    self._lower_expr(expr.func, prog)
                elif isinstance(expr.func, ast.Attribute):
                    self._lower_expr(expr.func.value, prog)
                    prog.emit(IRKind.CONST, a=expr.func.attr)
                    prog.emit(IRKind.GETATTR)
                else:
                    raise IRBuildError(f"Unsupported call target at line {self._lineno(expr)}")
                for a in expr.args:
                    self._lower_expr(a, prog)
                for kw in expr.keywords:
                    self._lower_expr(kw.value, prog)
                prog.emit(IRKind.CALL_FUNC_KW, a=len(expr.args),
                          b=tuple(kw.arg for kw in expr.keywords))
            elif isinstance(expr.func, ast.Name) and expr.func.id in _VM_SAFE_BUILTINS:
                for a in expr.args:
                    self._lower_expr(a, prog)
                prog.emit(IRKind.CALL_BUILTIN, a=expr.func.id, b=len(expr.args))
            elif isinstance(expr.func, ast.Attribute):
                self._lower_expr(expr.func.value, prog)
                prog.emit(IRKind.CONST, a=expr.func.attr)
                prog.emit(IRKind.GETATTR)
                for a in expr.args:
                    self._lower_expr(a, prog)
                prog.emit(IRKind.CALL_FUNC, a=len(expr.args))
            elif isinstance(expr.func, ast.Name):
                self._lower_expr(expr.func, prog)
                for a in expr.args:
                    self._lower_expr(a, prog)
                prog.emit(IRKind.CALL_FUNC, a=len(expr.args))
            else:
                raise IRBuildError(f"Unsupported call target at line {self._lineno(expr)}")
        elif isinstance(expr, ast.Dict):
            if any(k is None for k in expr.keys):
                raise IRBuildError(f"Dict **-unpacking is not supported at line {self._lineno(expr)}")
            for k, v in zip(expr.keys, expr.values):
                self._lower_expr(k, prog)
                self._lower_expr(v, prog)
            prog.emit(IRKind.BUILD_MAP, a=2 * len(expr.keys))
        elif isinstance(expr, ast.Set):
            for elt in expr.elts:
                self._lower_expr(elt, prog)
            prog.emit(IRKind.BUILD_SET, a=len(expr.elts))
        elif isinstance(expr, ast.JoinedStr):
            n_parts = 0
            for part in expr.values:
                if isinstance(part, ast.Constant):
                    if not isinstance(part.value, str):
                        raise IRBuildError(
                            f"Unsupported f-string constant part at line {self._lineno(expr)}")
                    self._lower_expr(part, prog)
                elif isinstance(part, ast.FormattedValue):
                    self._lower_formatted_value(part, prog)
                else:
                    raise IRBuildError(f"Unsupported f-string part "
                                       f"at line {self._lineno(expr)}")
                n_parts += 1
            prog.emit(IRKind.BUILD_STR, a=n_parts)
        elif isinstance(expr, ast.List):
            for elt in expr.elts:
                self._lower_expr(elt, prog)
            prog.emit(IRKind.BUILD_LIST, a=len(expr.elts))
        elif isinstance(expr, ast.Tuple):
            for elt in expr.elts:
                self._lower_expr(elt, prog)
            prog.emit(IRKind.BUILD_TUPLE, a=len(expr.elts))
        elif isinstance(expr, ast.Subscript):
            self._lower_expr(expr.value, prog)
            slice_node = expr.slice
            if isinstance(slice_node, ast.Slice):
                if slice_node.lower is None:
                    prog.emit(IRKind.CONST, a=None)
                else:
                    self._lower_expr(slice_node.lower, prog)
                if slice_node.upper is None:
                    prog.emit(IRKind.CONST, a=None)
                else:
                    self._lower_expr(slice_node.upper, prog)
                if slice_node.step is None:
                    prog.emit(IRKind.CONST, a=None)
                else:
                    self._lower_expr(slice_node.step, prog)
                prog.emit(IRKind.BUILD_SLICE)
            else:
                self._lower_expr(slice_node, prog)
            prog.emit(IRKind.SUBSCRIPT)
        elif isinstance(expr, ast.Attribute):
            if not isinstance(expr.ctx, ast.Load):
                raise IRBuildError(f"Attribute store is not supported at line {self._lineno(expr)}")
            self._lower_expr(expr.value, prog)
            prog.emit(IRKind.CONST, a=expr.attr)
            prog.emit(IRKind.GETATTR)
        elif isinstance(expr, ast.IfExp):
            else_label = self._new_label("ifexp_else")
            end_label = self._new_label("ifexp_end")
            self._lower_expr(expr.test, prog)
            prog.emit(IRKind.JUMP_IF_FALSE, target=else_label)
            self._lower_expr(expr.body, prog)
            prog.emit(IRKind.JUMP, target=end_label)
            prog.emit(IRKind.LABEL, target=else_label)
            self._lower_expr(expr.orelse, prog)
            prog.emit(IRKind.LABEL, target=end_label)
        elif isinstance(expr, ast.NamedExpr):
            if not isinstance(expr.target, ast.Name):
                raise IRBuildError("unsupported assignment-expression target "
                                   f"at line {self._lineno(expr)}")
            self._lower_expr(expr.value, prog)
            if expr.target.id in self._global_names:
                prog.emit(IRKind.STORE_GLOBAL, expr.target.id)
                prog.emit(IRKind.LOAD_GLOBAL, a=expr.target.id)
            else:
                prog.emit(IRKind.STORE, target=expr.target.id)
                prog.emit(IRKind.LOAD, a=expr.target.id)
        elif isinstance(expr, (ast.ListComp, ast.SetComp, ast.DictComp)):
            self._lower_comprehension(expr, prog)
        else:
            raise IRBuildError(f"Unsupported expression type: {type(expr).__name__} "
                                f"at line {self._lineno(expr)}")
class IRPass(ABC):
    name: str = "unnamed_pass"
    @abstractmethod
    def run(self, prog: IRProgram) -> IRProgram:
        ...
class ConstFoldPass(IRPass):
    name = "const_fold"
    _FOLD_OPS = {
        "+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
        "/": lambda a, b: a / b, "//": lambda a, b: a // b, "%": lambda a, b: a % b,
        "**": lambda a, b: a ** b, "&": lambda a, b: a & b, "|": lambda a, b: a | b,
        "^": lambda a, b: a ^ b, "<<": lambda a, b: a << b, ">>": lambda a, b: a >> b,
    }
    _DIV_FAMILY = {"/", "//", "%"}
    def run(self, prog: IRProgram) -> IRProgram:
        out = IRProgram()
        i = 0
        instrs = prog.instructions
        is_num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
        while i < len(instrs):
            if (i + 2 < len(instrs) and instrs[i].kind == IRKind.CONST
                    and instrs[i + 1].kind == IRKind.CONST
                    and instrs[i + 2].kind == IRKind.BINOP):
                a, b, op = instrs[i].a, instrs[i + 1].a, instrs[i + 2].a
                if (is_num(a) and is_num(b) and op in self._FOLD_OPS
                        and not (op in self._DIV_FAMILY and b == 0)):
                    try:
                        folded = self._FOLD_OPS[op](a, b)
                    except (OverflowError, ValueError, ZeroDivisionError):
                        pass
                    else:
                        out.emit(IRKind.CONST, a=folded)
                        i += 3
                        continue
            out.instructions.append(instrs[i])
            i += 1
        return out
class IRToVM:
    def __init__(self):
        self._BINOP_TO_OPCODE = {
            "+": Opcode.ADD, "-": Opcode.SUB, "*": Opcode.MUL, "/": Opcode.DIV,
            "//": Opcode.FLOORDIV, "%": Opcode.MOD, "**": Opcode.POW,
            "&": Opcode.BAND, "|": Opcode.BOR, "^": Opcode.BXOR,
            "<<": Opcode.LSHIFT, ">>": Opcode.RSHIFT,
        }
        self._CMP_TO_OPCODE = {
            "==": Opcode.EQ, "!=": Opcode.NEQ, "<": Opcode.LT,
            "<=": Opcode.LTE, ">": Opcode.GT, ">=": Opcode.GTE,
            "in": Opcode.CONTAINS, "not in": Opcode.NOT_CONTAINS,
            "is": Opcode.IS, "is not": Opcode.IS_NOT,
        }
        self._UNARY_TO_OPCODE = {
            "not": Opcode.NOT, "-": Opcode.NEG, "+": Opcode.POS, "~": Opcode.INVERT,
        }
    def lower(self, ir_prog: IRProgram) -> "Program":
        vm_prog = Program()
        label_positions: Dict[str, int] = {}
        pending_jumps: List[Tuple[int, str]] = []
        _ins = ir_prog.instructions
        _i = 0
        while _i < len(_ins):
            instr = _ins[_i]
            if instr.kind == IRKind.LABEL:
                label_positions[instr.target] = len(vm_prog.instructions)
                _i += 1
                continue
            elif instr.kind == IRKind.CONST and _i + 1 < len(_ins) \
                    and _ins[_i + 1].kind == IRKind.STORE \
                    and isinstance(_ins[_i + 1].target, str):
                vm_prog.append(Opcode.CONST_STORE, (instr.a, _ins[_i + 1].target))
                _i += 2
                continue
            elif instr.kind == IRKind.CONST:
                vm_prog.append(Opcode.LOAD_CONST, instr.a)
            elif instr.kind == IRKind.LOAD:
                vm_prog.append(Opcode.LOAD_VAR, instr.a)
            elif instr.kind == IRKind.LOAD_GLOBAL:
                vm_prog.append(Opcode.LOAD_VAR_G, instr.a)
            elif instr.kind == IRKind.STORE:
                vm_prog.append(Opcode.STORE_VAR, instr.target)
            elif instr.kind == IRKind.BINOP:
                if instr.a not in self._BINOP_TO_OPCODE:
                    raise IRBuildError(f"IRToVM: unmapped binop tag {instr.a!r}")
                vm_prog.append(self._BINOP_TO_OPCODE[instr.a])
            elif instr.kind == IRKind.UNARYOP:
                if instr.a not in self._UNARY_TO_OPCODE:
                    raise IRBuildError(f"IRToVM: unmapped unary tag {instr.a!r}")
                vm_prog.append(self._UNARY_TO_OPCODE[instr.a])
            elif instr.kind == IRKind.COMPARE:
                if instr.a not in self._CMP_TO_OPCODE:
                    raise IRBuildError(f"IRToVM: unmapped compare tag {instr.a!r}")
                vm_prog.append(self._CMP_TO_OPCODE[instr.a])
            elif instr.kind == IRKind.BOOL_AND_TEST:
                idx = len(vm_prog.instructions)
                vm_prog.append(Opcode.JUMP_IF_FALSE_OR_POP, None)
                pending_jumps.append((idx, instr.target))
            elif instr.kind == IRKind.BOOL_OR_TEST:
                idx = len(vm_prog.instructions)
                vm_prog.append(Opcode.JUMP_IF_TRUE_OR_POP, None)
                pending_jumps.append((idx, instr.target))
            elif instr.kind == IRKind.POP:
                vm_prog.append(Opcode.POP_TOP)
            elif instr.kind == IRKind.DUP_TOP:
                vm_prog.append(Opcode.DUP_TOP)
            elif instr.kind == IRKind.BUILD_LIST:
                vm_prog.append(Opcode.BUILD_LIST, instr.a)
            elif instr.kind == IRKind.BUILD_TUPLE:
                vm_prog.append(Opcode.BUILD_TUPLE, instr.a)
            elif instr.kind == IRKind.SUBSCRIPT:
                vm_prog.append(Opcode.BINARY_SUBSCR)
            elif instr.kind == IRKind.STORE_SUBSCR:
                vm_prog.append(Opcode.STORE_SUBSCR)
            elif instr.kind == IRKind.BUILD_MAP:
                vm_prog.append(Opcode.BUILD_MAP, instr.a)
            elif instr.kind == IRKind.BUILD_SET:
                vm_prog.append(Opcode.BUILD_SET, instr.a)
            elif instr.kind == IRKind.CALL_FUNC:
                vm_prog.append(Opcode.CALL_FUNC, instr.a)
            elif instr.kind == IRKind.BUILD_SLICE:
                vm_prog.append(Opcode.BUILD_SLICE)
            elif instr.kind == IRKind.CONVERT:
                vm_prog.append(Opcode.CONVERT, int(instr.a))
            elif instr.kind == IRKind.BUILD_STR:
                vm_prog.append(Opcode.BUILD_STR, instr.a)
            elif instr.kind == IRKind.FORMAT_SPEC:
                vm_prog.append(Opcode.FORMAT_SPEC, 0)
            elif instr.kind == IRKind.GETATTR:
                vm_prog.append(Opcode.GETATTR)
            elif instr.kind == IRKind.CALL_BUILTIN:
                vm_prog.append(Opcode.CALL_BUILTIN, (instr.a, instr.b))
            elif instr.kind == IRKind.CALL_PRINT:
                vm_prog.append(Opcode.PRINT, int(instr.a or 0))
            elif instr.kind == IRKind.RETURN:
                vm_prog.append(Opcode.RETURN)
            elif instr.kind == IRKind.STORE_ATTR:
                vm_prog.append(Opcode.STORE_ATTR, instr.a)
            elif instr.kind == IRKind.STORE_GLOBAL:
                vm_prog.append(Opcode.STORE_GLOBAL, instr.a)
            elif instr.kind == IRKind.UNPACK_SEQUENCE:
                vm_prog.append(Opcode.UNPACK_SEQUENCE, int(instr.a))
            elif instr.kind == IRKind.RAISE:
                vm_prog.append(Opcode.RAISE)
            elif instr.kind == IRKind.DELETE_NAME:
                vm_prog.append(Opcode.DELETE_NAME, instr.a)
            elif instr.kind == IRKind.DELETE_ATTR:
                vm_prog.append(Opcode.DELETE_ATTR)
            elif instr.kind == IRKind.DELETE_SUBSCR:
                vm_prog.append(Opcode.DELETE_SUBSCR)
            elif instr.kind == IRKind.DELETE_GLOBAL:
                vm_prog.append(Opcode.DELETE_GLOBAL, instr.a)
            elif instr.kind == IRKind.IMPORT_NAME:
                vm_prog.append(Opcode.IMPORT_NAME, instr.a)
            elif instr.kind == IRKind.GET_ITER:
                vm_prog.append(Opcode.GET_ITER)
            elif instr.kind == IRKind.FOR_ITER:
                idx = len(vm_prog.instructions)
                vm_prog.append(Opcode.FOR_ITER, None)
                pending_jumps.append((idx, instr.target))
            elif instr.kind == IRKind.LIST_APPEND:
                vm_prog.append(Opcode.LIST_APPEND)
            elif instr.kind == IRKind.SET_ADD:
                vm_prog.append(Opcode.SET_ADD)
            elif instr.kind == IRKind.MAP_ADD:
                vm_prog.append(Opcode.MAP_ADD)
            elif instr.kind == IRKind.CALL_FUNC_KW:
                vm_prog.append(Opcode.CALL_FUNC_KW, (instr.a, instr.b))
            elif instr.kind == IRKind.SETUP_FIN:
                idx = len(vm_prog.instructions)
                vm_prog.append(Opcode.SETUP_FIN, None)
                pending_jumps.append((idx, instr.target))
            elif instr.kind == IRKind.POP_FIN:
                vm_prog.append(Opcode.POP_FIN)
            elif instr.kind == IRKind.EXC_MATCH:
                vm_prog.append(Opcode.EXC_MATCH, (instr.a, instr.b))
            elif instr.kind == IRKind.NOP:
                vm_prog.append(Opcode.NOP)
            elif instr.kind == IRKind.JUMP:
                idx = len(vm_prog.instructions)
                vm_prog.append(Opcode.JUMP, None)
                pending_jumps.append((idx, instr.target))
            elif instr.kind == IRKind.JUMP_IF_FALSE:
                idx = len(vm_prog.instructions)
                vm_prog.append(Opcode.JUMP_IF_FALSE, None)
                pending_jumps.append((idx, instr.target))
            elif instr.kind == IRKind.JUMP_IF_FALSE_OR_POP:
                idx = len(vm_prog.instructions)
                vm_prog.append(Opcode.JUMP_IF_FALSE_OR_POP, None)
                pending_jumps.append((idx, instr.target))
            else:
                raise IRBuildError(f"Unsupported IR instruction kind: {instr.kind}")
            _i += 1
        for idx, label in pending_jumps:
            target_pos = label_positions.get(label)
            if target_pos is None:
                raise IRBuildError(f"Unresolved IR label: {label}")
            vm_prog.instructions[idx].operand = target_pos - idx
        vm_prog.append(Opcode.HALT)
        return vm_prog
class Opcode(Enum):
    LOAD_CONST = auto()
    LOAD_VAR = auto()
    LOAD_VAR_G = auto()
    STORE_VAR = auto()
    ADD = auto()
    SUB = auto()
    MUL = auto()
    DIV = auto()
    FLOORDIV = auto()
    MOD = auto()
    POW = auto()
    BAND = auto()
    BOR = auto()
    BXOR = auto()
    LSHIFT = auto()
    RSHIFT = auto()
    EQ = auto()
    NEQ = auto()
    LT = auto()
    LTE = auto()
    GT = auto()
    GTE = auto()
    NOT = auto()
    NEG = auto()
    POS = auto()
    INVERT = auto()
    JUMP_IF_FALSE_OR_POP = auto()
    JUMP_IF_TRUE_OR_POP = auto()
    POP_TOP = auto()
    BUILD_LIST = auto()
    BUILD_TUPLE = auto()
    BINARY_SUBSCR = auto()
    STORE_SUBSCR = auto()
    BUILD_MAP = auto()
    CALL_FUNC = auto()
    GETATTR = auto()
    CONTAINS = auto()
    NOT_CONTAINS = auto()
    IS = auto()
    IS_NOT = auto()
    BUILD_SET = auto()
    BUILD_SLICE = auto()
    CONVERT = auto()
    BUILD_STR = auto()
    FORMAT_SPEC = auto()
    CALL_BUILTIN = auto()
    PRINT = auto()
    JUMP = auto()
    JUMP_IF_FALSE = auto()
    RETURN = auto()
    NOP = auto()
    HALT = auto()
    STORE_ATTR = auto()
    STORE_GLOBAL = auto()
    UNPACK_SEQUENCE = auto()
    RAISE = auto()
    DELETE_NAME = auto()
    DELETE_ATTR = auto()
    DELETE_SUBSCR = auto()
    DELETE_GLOBAL = auto()
    IMPORT_NAME = auto()
    GET_ITER = auto()
    FOR_ITER = auto()
    LIST_APPEND = auto()
    SET_ADD = auto()
    MAP_ADD = auto()
    CALL_FUNC_KW = auto()
    SETUP_FIN = auto()
    POP_FIN = auto()
    EXC_MATCH = auto()
    DUP_TOP = auto()
    CONST_STORE = auto()
@dataclass
class Instruction:
    opcode: Opcode
    operand: Union[Any, None] = None
    def __repr__(self):
        if self.operand is not None:
            return f"{self.opcode.name} {self.operand!r}"
        return self.opcode.name
@dataclass
class Program:
    instructions: List[Instruction] = field(default_factory=list)
    def append(self, opcode: Opcode, operand=None):
        self.instructions.append(Instruction(opcode, operand))
        return self
class VMCompileError(Exception):
    pass
_BUILTIN_ORDER: List[str] = [
    "len", "str", "int", "float", "bool",
    "abs", "min", "max", "sum", "round",
    "sorted", "list", "tuple",
]
_VM_SAFE_BUILTINS: Dict[str, Callable] = {n: getattr(builtins, n) for n in _BUILTIN_ORDER}
_VM_JUMP_OPS = frozenset({
    Opcode.JUMP, Opcode.JUMP_IF_FALSE,
    Opcode.JUMP_IF_FALSE_OR_POP, Opcode.JUMP_IF_TRUE_OR_POP,
    Opcode.FOR_ITER, Opcode.SETUP_FIN,
})
_VM_BINOP_OPERATOR = {
    Opcode.ADD: "add", Opcode.SUB: "sub", Opcode.MUL: "mul",
    Opcode.DIV: "truediv", Opcode.FLOORDIV: "floordiv", Opcode.MOD: "mod",
    Opcode.POW: "pow", Opcode.BAND: "and_", Opcode.BOR: "or_",
    Opcode.BXOR: "xor", Opcode.LSHIFT: "lshift", Opcode.RSHIFT: "rshift",
    Opcode.EQ: "eq", Opcode.NEQ: "ne", Opcode.LT: "lt",
    Opcode.LTE: "le", Opcode.GT: "gt", Opcode.GTE: "ge",
}
_VM_UNARY_ATTR = {
    Opcode.NEG: "_op.neg", Opcode.POS: "_op.pos", Opcode.INVERT: "_op.invert",
}
def _permute_opcodes(build_seed: int) -> Dict[Opcode, int]:
    ops = list(Opcode)
    ids = list(range(len(ops)))
    _seeded_rng(build_seed, "vm_opcode_permutation").shuffle(ids)
    return {op: ids[i] for i, op in enumerate(ops)}
def _vm_region_mask(build_seed: int, func_name: str, base_mask: int) -> int:
    d = hashlib.sha256(
        f"{build_seed}::vm_region::{func_name}".encode()).digest()
    return base_mask ^ int.from_bytes(d[:4], "big")
def _serialize_vm_program(program: Program, perm: Dict[Opcode, int],
                          consts: List[Any], cidx: Dict[Tuple[str, Any], int],
                          operand_mask: int = 0,
                          ) -> Tuple[Tuple[int, ...], Dict[str, int], Set[Opcode], List[int]]:
    flat: List[int] = []
    slots: Dict[str, int] = {}
    used: Set[Opcode] = set()
    pair_idxs: List[int] = []
    def _cslot(v: Any) -> int:
        key = (type(v).__name__, v)
        j = cidx.get(key)
        if j is None:
            j = len(consts)
            consts.append(v)
            cidx[key] = j
        return j
    def _vslot(name: str) -> int:
        j = slots.get(name)
        if j is None:
            j = len(slots)
            slots[name] = j
        return j
    m = operand_mask
    for _iidx, ins in enumerate(program.instructions):
        op = ins.opcode
        used.add(op)
        oid = perm[op]
        if op is Opcode.LOAD_CONST:
            arg = _cslot(ins.operand)
        elif op in (Opcode.LOAD_VAR, Opcode.STORE_VAR, Opcode.LOAD_VAR_G,
                      Opcode.STORE_ATTR, Opcode.STORE_GLOBAL,
                      Opcode.DELETE_NAME, Opcode.DELETE_GLOBAL):
            arg = _vslot(ins.operand)
        elif op in (Opcode.CALL_FUNC_KW, Opcode.IMPORT_NAME):
            arg = _cslot(ins.operand)
        elif op is Opcode.CONST_STORE:
            _pi = _cslot((_cslot(ins.operand[0]), _vslot(ins.operand[1])))
            pair_idxs.append(_pi)
            arg = _pi
        elif op is Opcode.CALL_BUILTIN:
            nm, cnt = ins.operand
            arg = (_BUILTIN_ORDER.index(nm) << 4) | int(cnt)
        elif op is Opcode.BUILD_LIST:
            arg = int(ins.operand) << 1
        elif op is Opcode.BUILD_TUPLE:
            arg = (int(ins.operand) << 1) | 1
        elif op is Opcode.BUILD_MAP:
            arg = int(ins.operand) if ins.operand else -1
        elif op is Opcode.CALL_FUNC:
            arg = int(ins.operand)
        elif op is Opcode.PRINT:
            arg = int(ins.operand) if ins.operand else -1
        elif op is Opcode.BUILD_SET:
            arg = int(ins.operand)
        elif op is Opcode.CONVERT:
            arg = int(ins.operand)
        elif op is Opcode.BUILD_STR:
            arg = int(ins.operand)
        elif op is Opcode.FORMAT_SPEC:
            arg = 0
        elif op in _VM_JUMP_OPS:
            arg = int(ins.operand) * 2
        elif op is Opcode.UNPACK_SEQUENCE:
            arg = int(ins.operand)
        else:
            arg = 0
        flat.append(oid)
        flat.append(arg ^ (m ^ (_iidx & 255)))
    return tuple(flat), slots, used, pair_idxs
def _mask_vm_consts(consts: List[Any], mask: int) -> Tuple[Any, ...]:
    out: List[Any] = []
    for idx, v in enumerate(consts):
        if type(v) is int:
            out.append(v ^ mask ^ (idx & 255))
        elif isinstance(v, str):
            kb = ((mask >> (8 * (idx % 4))) & 255) ^ ((idx * 31) & 255)
            data = v.encode("utf-8")
            enc = bytes(b ^ kb ^ (j & 255) for j, b in enumerate(data))
            out.append(enc.decode("latin-1"))
        elif isinstance(v, (bytes, bytearray)):
            kb = ((mask >> (8 * (idx % 4))) & 255) ^ ((idx * 31) & 255)
            data = bytes(v)
            out.append(bytes(b ^ kb ^ (j & 255) for j, b in enumerate(data)))
        else:
            out.append(v)
    return tuple(out)
def _vm_runtime_helper_source(func_name: str, perm: Dict[Opcode, int],
                              used: Set[Opcode], operand_mask: int = 0,
                              shuffle_rng: Optional["random.Random"] = None,
                              build_seed: int = 0,
                              dispatch_mode: str = "flat") -> str:
    p = perm
    m = operand_mask
    rng = shuffle_rng if shuffle_rng is not None else _seeded_rng(build_seed, "vm_handler_order")
    pc_mask = int.from_bytes(
        hashlib.sha256(f"{build_seed}::vm_pc_mask".encode()).digest()[:4], "big")
    _vm_fail = hashlib.sha256(f"{build_seed}::vm_fail".encode()).hexdigest()[:8]
    name_rng = _seeded_rng(build_seed, "vm_internal_identifiers")
    used_names: Set[str] = set()
    reserved = set(keyword.kwlist) | set(getattr(keyword, "softkwlist", []) or [])
    def _nid(canonical: str) -> str:
        while True:
            cand = "".join(name_rng.choice(HIRAGANA_POOL)
                           for _ in range(3))
            if cand not in used_names and cand not in reserved:
                used_names.add(cand)
                return cand
    N = {k: _nid(k) for k in
         ("prog", "consts", "args", "ntab", "gtab", "btab2", "bimod",
          "cvtab", "hs", "w1",
          "stack", "vars", "pc", "unpc",
          "ret", "opc", "arg", "fn", "btab", "utab", "tmp", "y", "x",
          "om", "km", "opmod", "tmask")}
    s, v, k, u, o, q = N["stack"], N["vars"], N["pc"], N["unpc"], N["opc"], N["arg"]
    f, bt, ut, t, y, x = N["fn"], N["btab"], N["utab"], N["tmp"], N["y"], N["x"]
    om, km = N["om"], N["km"]
    nt, gg, bd, bi, cv = (N["ntab"], N["gtab"], N["btab2"], N["bimod"],
                          N["cvtab"])
    _opmod = N["opmod"]
    _live_pids = sorted(perm[op] for op in used if op in perm)
    _mrng = _seeded_rng(build_seed, "vm_eq_mask")
    _mask_cands = [0xFFFFFFF, 0x3FFFFFFF, 0xFFFFFF, 0xFFFFFFFF, 0xFFFF]
    _mrng.shuffle(_mask_cands)
    _EQM = next((m for m in _mask_cands
                 if len({pid & m for pid in _live_pids}) == len(_live_pids)),
                0xFFFFFFF)
    def _eqtest(opid: int) -> str:
        form = rng.randrange(4)
        if form == 0:
            return f"{o}=={opid}"
        if form == 1:
            return f"not {o}^{opid}"
        if form == 2:
            return f"{opid}<={o}<={opid}"
        return f"({o}&{_EQM})==({opid}&{_EQM})"
    L: List[str] = []
    L.append(f"def {func_name}({N['prog']},{N['consts']},{nt},{N['args']},{N['tmask']}):")
    has_bin = any(op in used for op in _VM_BINOP_OPERATOR)
    has_una = (any(op in used for op in _VM_UNARY_ATTR)
               or Opcode.NOT in used)
    _h_operator = _hidden_name_src("operator", build_seed, "hidmod")
    _h_bi_vm = _hidden_name_src("builtins", build_seed, "hidmod")
    L.append(f"    {_opmod}=__import__({_h_operator})")
    L.append(f"    {om}={m};{km}={pc_mask}")
    L.append(f"    {s}=[];{v}=dict({N['args']});{k}={km};{N['ret']}=None;{N['hs']}=[]")
    L.append(f"    {gg}=globals()")
    _h_bi_mod = _hidden_name_src("builtins", build_seed, "hidmod")
    L.append(f"    {bi}=__import__({_h_bi_mod});{bd}={bi}.__dict__")
    if Opcode.CONVERT in used:
        L.append(f"    {cv}=(lambda _v:type(_v).__format__(_v,''),"
                 f"str,repr,ascii,bool)")
    if has_bin:
        _bin_entries = []
        for _bi, (_bop, _battr) in enumerate(_VM_BINOP_OPERATOR.items()):
            _opid = p[_bop]
            _kd = ((_opid * 37 + (m & 255) + 11) & 255)
            _enc = bytes(b ^ _kd for b in _battr.encode())
            _expr = (f"getattr({_opmod},__import__({_h_bi_vm}).bytes("
                     f"[b^((({_opid}*37+({om}&255))+11)&255) for b in {list(_enc)}]).decode())")
            _bin_entries.append(f"{_opid}:{_expr}")
        rng.shuffle(_bin_entries)
        L.append(f"    {bt}={{{','.join(_bin_entries)}}}")
    if has_una:
        parts = [f"{p[Opcode.NOT]}:(lambda x:not x)"]
        _upart = []
        for _ui, (_uop, _uattr) in enumerate(_VM_UNARY_ATTR.items()):
            _aname = _uattr.split(".")[-1]
            _uopid = p[_uop]
            _kd2 = ((_uopid * 37 + (m & 255) + 11) & 255)
            _enc2 = bytes(b ^ _kd2 for b in _aname.encode())
            _uexpr = (f"getattr({_opmod},__import__({_h_bi_vm}).bytes("
                      f"[b^((({_uopid}*37+({om}&255))+11)&255) for b in {list(_enc2)}]).decode())")
            _upart.append(f"{_uopid}:{_uexpr}")
        rng.shuffle(_upart)
        parts.extend(_upart)
        L.append(f"    {ut}={{{','.join(parts)}}}")
    L.append("    while 1:")
    L.append(f"        {u}={k}^{km}")
    L.append(f"        {o}={N['prog']}[{u}];{q}={N['prog']}[{u}+1]^({N['tmask']}^(({u}>>1)&255))")
    first = True
    def emit(cond: str, body: str):
        nonlocal first
        kw = "if" if first else "elif"
        first = False
        L.append(f"        {kw} {cond}:{body}")
    def adv(extra: str = "2") -> str:
        return f"{k}=({u}+{extra})^{km}"
    if has_bin:
        L.append(f"        {f}={bt}.get({o})")
        L.append("        if %s is not None:" % f)
        L.append(f"            {y}={s}.pop();{x}={s}.pop();{s}.append({f}({x},{y}));{adv()};continue")
        if has_una:
            L.append(f"        {f}={ut}.get({o})")
    elif has_una:
        L.append(f"        {f}={ut}.get({o})")
    if has_una:
        L.append("        if %s is not None:" % f)
        L.append(f"            {s}.append({f}({s}.pop()));{adv()};continue")
    first = True
    spec: List[Tuple[int, Tuple[int, ...], str]] = []
    if Opcode.LOAD_CONST in used:
        spec.append((0, (p[Opcode.LOAD_CONST],),
                      f"{t}={N['consts']}[{q}];"
                      f"{y}=(({om}>>(8*({q}%4)))&255)^(({q}*31)&255);"
                      f"{s}.append(({t}^{om}^({q}&255) if type({t}) is int else "
                      f"(bytes(_c^{y}^(_j&255) for _j,_c in enumerate({t})) if type({t}) is bytes else "
                      f"(bytes(_c^{y}^(_j&255) for _j,_c in enumerate({t}.encode('latin-1'))).decode('utf-8') if type({t}) is str else {t}))));"
                      f"{adv()}"))
    if Opcode.CONST_STORE in used:
        spec.append((49, (p[Opcode.CONST_STORE],),
                      f"{t}={N['consts']}[{q}];{x}={t}[1];{y}={t}[0];{t}={N['consts']}[{y}];"
                      f"{N['w1']}=(({om}>>(8*({y}%4)))&255)^(({y}*31)&255);"
                      f"{v}[{x}]=({t}^{om}^({y}&255) if type({t}) is int else "
                      f"(bytes(_c^{N['w1']}^(_j&255) for _j,_c in enumerate({t})) if type({t}) is bytes else "
                      f"(bytes(_c^{N['w1']}^(_j&255) for _j,_c in enumerate({t}.encode('latin-1'))).decode('utf-8') if type({t}) is str else {t})));"
                      f"{adv()}"))
    if Opcode.LOAD_VAR in used:
        spec.append((1, (p[Opcode.LOAD_VAR],),
                     f"{s}.append({v}[{q}]);{adv()}"))
    if Opcode.LOAD_VAR_G in used:
        spec.append((17, (p[Opcode.LOAD_VAR_G],),
                     f"{y}={nt}[{q}];"
                     f"{s}.append({gg}[{y}] if {y} in {gg} else {bd}[{y}]);{adv()}"))
    if Opcode.STORE_VAR in used:
        spec.append((2, (p[Opcode.STORE_VAR],),
                     f"{v}[{q}]={s}.pop();{adv()}"))
    if Opcode.JUMP in used:
        spec.append((3, (p[Opcode.JUMP],), f"{k}=({u}+{q})^{km}"))
    if Opcode.JUMP_IF_FALSE in used:
        spec.append((4, (p[Opcode.JUMP_IF_FALSE],),
                     f"{y}={s}.pop();{k}=({u}+({q} if not {y} else 2))^{km}"))
    if Opcode.JUMP_IF_FALSE_OR_POP in used:
        spec.append((5, (p[Opcode.JUMP_IF_FALSE_OR_POP],),
                     f"{k}=({u}+({q} if not {s}[-1] else ({s}.pop(),2)[1]))^{km}"))
    if Opcode.JUMP_IF_TRUE_OR_POP in used:
        spec.append((6, (p[Opcode.JUMP_IF_TRUE_OR_POP],),
                     f"{k}=({u}+({q} if {s}[-1] else ({s}.pop(),2)[1]))^{km}"))
    if Opcode.POP_TOP in used:
        spec.append((7, (p[Opcode.POP_TOP],), f"{s}.pop();{adv()}"))
    both_collections = (Opcode.BUILD_LIST in used) and (Opcode.BUILD_TUPLE in used)
    merged_collection_body = (
        f"{t}={s}[len({s})-({q}>>1):] if ({q}>>1) else [];"
        f"{s}[len({s})-({q}>>1):]=[];"
        f"{s}.append(tuple({t}) if {q}&1 else list({t}));{adv()}")
    if both_collections and dispatch_mode != "grouped":
        spec.append((8, (p[Opcode.BUILD_LIST], p[Opcode.BUILD_TUPLE]),
                     merged_collection_body))
    else:
        if Opcode.BUILD_LIST in used:
            spec.append((8, (p[Opcode.BUILD_LIST],),
                         f"{t}={s}[len({s})-({q}>>1):] if ({q}>>1) else [];"
                         f"{s}[len({s})-({q}>>1):]=[];"
                         f"{s}.append(list({t}));{adv()}"))
        if Opcode.BUILD_TUPLE in used:
            spec.append((9, (p[Opcode.BUILD_TUPLE],),
                         f"{t}={s}[len({s})-({q}>>1):] if ({q}>>1) else [];"
                         f"{s}[len({s})-({q}>>1):]=[];"
                         f"{s}.append(tuple({t}));{adv()}"))
    if Opcode.BINARY_SUBSCR in used:
        spec.append((10, (p[Opcode.BINARY_SUBSCR],),
                     f"{y}={s}.pop();{x}={s}.pop();{s}.append({x}[{y}]);{adv()}"))
    if Opcode.STORE_SUBSCR in used:
        spec.append((18, (p[Opcode.STORE_SUBSCR],),
                     f"{t}={s}.pop();{y}={s}.pop();{x}={s}.pop();{x}[{y}]={t};{adv()}"))
    if Opcode.BUILD_MAP in used:
        spec.append((19, (p[Opcode.BUILD_MAP],),
                     f"{t}={s}[len({s})-{q}:] if {q} else [];{s}[len({s})-{q}:]=[];"
                     f"{s}.append(dict(zip({t}[0::2],{t}[1::2])));{adv()}"))
    if Opcode.CALL_FUNC in used:
        spec.append((20, (p[Opcode.CALL_FUNC],),
                     f"{t}={s}[len({s})-({q}+1):];del {s}[len({s})-({q}+1):];"
                     f"{s}.append({t}[0](*{t}[1:]));{adv()}"))
    if Opcode.CONTAINS in used:
        spec.append((21, (p[Opcode.CONTAINS],),
                     f"{y}={s}.pop();{x}={s}.pop();{s}.append({x} in {y});{adv()}"))
    if Opcode.NOT_CONTAINS in used:
        spec.append((22, (p[Opcode.NOT_CONTAINS],),
                     f"{y}={s}.pop();{x}={s}.pop();{s}.append({x} not in {y});{adv()}"))
    if Opcode.IS in used:
        spec.append((23, (p[Opcode.IS],),
                     f"{y}={s}.pop();{x}={s}.pop();{s}.append({x} is {y});{adv()}"))
    if Opcode.IS_NOT in used:
        spec.append((24, (p[Opcode.IS_NOT],),
                     f"{y}={s}.pop();{x}={s}.pop();{s}.append({x} is not {y});{adv()}"))
    if Opcode.BUILD_SET in used:
        spec.append((25, (p[Opcode.BUILD_SET],),
                     f"{t}={s}[len({s})-{q}:] if {q} else [];{s}[len({s})-{q}:]=[];"
                     f"{s}.append(set({t}));{adv()}"))
    if Opcode.BUILD_SLICE in used:
        spec.append((26, (p[Opcode.BUILD_SLICE],),
                     f"{t}={s}[len({s})-3:];del {s}[len({s})-3:];"
                     f"{s}.append(slice({t}[0],{t}[1],{t}[2]));{adv()}"))
    if Opcode.CONVERT in used:
        spec.append((27, (p[Opcode.CONVERT],),
                     f"{t}={s}.pop();{s}.append({cv}[{q}]({t}));{adv()}"))
    if Opcode.BUILD_STR in used:
        spec.append((28, (p[Opcode.BUILD_STR],),
                     f"{t}={s}[len({s})-{q}:] if {q} else [];{s}[len({s})-{q}:]=[];"
                     f'{s}.append("".join({t}));{adv()}'))
    if Opcode.FORMAT_SPEC in used:
        spec.append((50, (p[Opcode.FORMAT_SPEC],),
                     f"{y}={s}.pop();{x}={s}.pop();"
                     f"{s}.append({x}.__format__({y}));{adv()}"))
    if Opcode.GETATTR in used:
        spec.append((16, (p[Opcode.GETATTR],),
                     f"{y}={s}.pop();{x}={s}.pop();{s}.append(getattr({x},{y}));{adv()}"))
    if Opcode.STORE_ATTR in used:
        spec.append((30, (p[Opcode.STORE_ATTR],),
                     f"{y}={nt}[{q}];{t}={s}.pop();{x}={s}.pop();setattr({x},{y},{t});{adv()}"))
    if Opcode.STORE_GLOBAL in used:
        spec.append((31, (p[Opcode.STORE_GLOBAL],),
                     f"{y}={nt}[{q}];{gg}[{y}]={s}.pop();{adv()}"))
    if Opcode.UNPACK_SEQUENCE in used:
        spec.append((32, (p[Opcode.UNPACK_SEQUENCE],),
                     f"{t}=list({s}.pop());"
                     f"({s}.extend(reversed({t})) if len({t})=={q} else "
                     f"(_ for _ in ()).throw(ValueError('too many values to unpack (expected '+str({q})+')' "
                     f"if len({t})>{q} else 'not enough values to unpack (expected '+str({q})+', got '+str(len({t}))+')')));"
                     f"{adv()}"))
    if Opcode.DUP_TOP in used:
        spec.append((33, (p[Opcode.DUP_TOP],),
                     f"{s}.append({s}[-1]);{adv()}"))
    if Opcode.RAISE in used:
        spec.append((34, (p[Opcode.RAISE],),
                     f"raise {s}.pop()"))
    if Opcode.DELETE_NAME in used:
        spec.append((35, (p[Opcode.DELETE_NAME],),
                     f"({v}.pop({q}) if {q} in {v} else "
                     f"(_ for _ in ()).throw(NameError(\"name '\"+{nt}[{q}]+\"' is not defined\")));"
                     f"{adv()}"))
    if Opcode.DELETE_GLOBAL in used:
        spec.append((36, (p[Opcode.DELETE_GLOBAL],),
                     f"({gg}.pop({nt}[{q}]) if {nt}[{q}] in {gg} else "
                     f"(_ for _ in ()).throw(NameError(\"name '\"+{nt}[{q}]+\"' is not defined\")));"
                     f"{adv()}"))
    if Opcode.DELETE_ATTR in used:
        spec.append((37, (p[Opcode.DELETE_ATTR],),
                     f"{y}={s}.pop();{x}={s}.pop();delattr({x},{y});{adv()}"))
    if Opcode.DELETE_SUBSCR in used:
        spec.append((38, (p[Opcode.DELETE_SUBSCR],),
                     f"{y}={s}.pop();{x}={s}.pop();del {x}[{y}];{adv()}"))
    if Opcode.IMPORT_NAME in used:
        spec.append((39, (p[Opcode.IMPORT_NAME],),
                     f"{t}={N['consts']}[{q}];"
                     f"{s}.append({bd}['__import__']({t}[0],{gg},{{}},list({t}[1]),0));{adv()}"))
    if Opcode.GET_ITER in used:
        spec.append((40, (p[Opcode.GET_ITER],),
                     f"{s}.append(iter({s}.pop()));{adv()}"))
    if Opcode.FOR_ITER in used:
        spec.append((41, (p[Opcode.FOR_ITER],),
                     f"{t}=next({s}[-1],{s});"
                     f"{k}=({u}+({q} if {t} is {s} else 2))^{km};"
                     f"{s}.append({t}) if {t} is not {s} else {s}.pop()"))
    if Opcode.LIST_APPEND in used:
        spec.append((42, (p[Opcode.LIST_APPEND],),
                     f"{t}={s}.pop();{s}[-1].append({t});{adv()}"))
    if Opcode.SET_ADD in used:
        spec.append((43, (p[Opcode.SET_ADD],),
                     f"{t}={s}.pop();{s}[-1].add({t});{adv()}"))
    if Opcode.MAP_ADD in used:
        spec.append((44, (p[Opcode.MAP_ADD],),
                     f"{t}={s}.pop();{y}={s}.pop();{s}[-1][{y}]={t};{adv()}"))
    if Opcode.CALL_FUNC_KW in used:
        spec.append((45, (p[Opcode.CALL_FUNC_KW],),
                     f"{t}={N['consts']}[{q}];{y}={t}[0]+len({t}[1]);{f}={s}[-{y}-1];"
                     f"{x}={s}[-{y}:];del {s}[-{y}-1:];"
                     f"{s}.append({f}(*{x}[:{y}-len({t}[1])],**dict(zip({t}[1],{x}[{y}-len({t}[1]):]))));{adv()}"))
    if Opcode.SETUP_FIN in used:
        spec.append((46, (p[Opcode.SETUP_FIN],),
                     f"{N['hs']}.append((len({s}),({u}+{q})^{km}));{adv()}"))
    if Opcode.POP_FIN in used:
        spec.append((47, (p[Opcode.POP_FIN],),
                     f"{N['hs']}.pop();{adv()}"))
    if Opcode.EXC_MATCH in used:
        spec.append((48, (p[Opcode.EXC_MATCH],),
                     f"{t}={s}.pop();{y}={s}.pop();"
                     f"({s}.append({y}) if isinstance({y},{t}) else (_ for _ in ()).throw({y}));{adv()}"))
    if Opcode.CALL_BUILTIN in used:
        bts = "(" + ",".join(_BUILTIN_ORDER) + ")"
        spec.append((11, (p[Opcode.CALL_BUILTIN],),
                     f"{t}={s}[len({s})-({q}&15):] if ({q}&15) else [];"
                     f"{s}[len({s})-({q}&15):]=[];"
                     f"{s}.append({bts}[{q}>>4](*{t}));{adv()}"))
    if Opcode.PRINT in used:
        spec.append((12, (p[Opcode.PRINT],),
                     f"{t}={s}[len({s})-{q}:] if {q} else [];{s}[len({s})-{q}:]=[];"
                     f"print(*{t});{adv()}"))
    if Opcode.RETURN in used:
        spec.append((13, (p[Opcode.RETURN],),
                     f"{N['ret']}={s}.pop() if {s} else None;break"))
    if Opcode.NOP in used:
        spec.append((14, (p[Opcode.NOP],), adv()))
    spec.append((15, (p[Opcode.HALT],), "break"))
    _all_ids = set(range(len(list(Opcode))))
    _live_ids = {opid for _, ids, _b in spec for opid in ids}
    _dead_pool = sorted(set(perm.values()) - _live_ids)
    _decoy_ids: List[int] = []
    if _dead_pool:
        _drng = _seeded_rng(build_seed, "vm_decoy_handlers")
        _decoy_ids = _drng.sample(_dead_pool, min(2, len(_dead_pool)))
        for _did in _decoy_ids:
            _decoy_body = f"raise RuntimeError('{_vm_fail}')"
            spec.append((99, (_did,), _decoy_body))
    if dispatch_mode == "grouped":
        vm_mod = 4 * (2 ** (derive_seed(build_seed, "vm_bucket_mod") % 3))
        buckets: Dict[int, List[Tuple[int, str]]] = {}
        for _, ids, body in spec:
            for opid in ids:
                buckets.setdefault(opid % vm_mod, []).append((opid, body))
        bucket_order = sorted(buckets.keys())
        rng.shuffle(bucket_order)
        for _bk_handlers in buckets.values():
            rng.shuffle(_bk_handlers)
        fb = True
        for bk in bucket_order:
            kw = "if" if fb else "elif"
            fb = False
            _bguard = f"{o}%{vm_mod}=={bk}"
            L.append(f"        {kw} {_bguard}:")
            fi = True
            for opid, body in buckets[bk]:
                kw2 = "if" if fi else "elif"
                fi = False
                L.append(f"            {kw2} {_eqtest(opid)}:{body}")
            L.append(f"            else:raise RuntimeError('{_vm_fail}')")
    elif dispatch_mode == "nested":
        _shift = 2 + (derive_seed(build_seed, "vm_nested_shift") % 3)
        _groups: Dict[int, List[Tuple[int, str]]] = {}
        for _, ids, body in spec:
            for opid in ids:
                _groups.setdefault(opid >> _shift, []).append((opid, body))
        _gorder = sorted(_groups.keys())
        rng.shuffle(_gorder)
        _fo = True
        for _g in _gorder:
            _kw = "if" if _fo else "elif"
            _fo = False
            L.append(f"        {_kw} ({o}>>{_shift})=={_g}:")
            _fi = True
            for opid, body in _groups[_g]:
                _kw2 = "if" if _fi else "elif"
                _fi = False
                L.append(f"            {_kw2} {_eqtest(opid)}:{body}")
            L.append(f"            else:raise RuntimeError('{_vm_fail}')")
        L.append(f"        else:raise RuntimeError('{_vm_fail}')")
    else:
        entries = [(ids, body) for _, ids, body in spec]
        rng.shuffle(entries)
        for ids, body in entries:
            cond = "(" + " or ".join(_eqtest(i) for i in ids) + ")"
            emit(cond, body)
        L.append(f"        else:raise RuntimeError('{_vm_fail}')")
    L.append(f"    return {N['ret']}")
    if Opcode.SETUP_FIN in used:
        _wi = next(i for i, _x in enumerate(L) if _x.strip() == "while 1:")
        _ret_i = next(i for i, _x in enumerate(L) if _x.strip().startswith("return "))
        for _j in range(_wi + 1, _ret_i):
            if L[_j].strip():
                L[_j] = "    " + L[_j]
        _e = t
        L[_ret_i:_ret_i] = [
            "        except Exception as " + _e + ":",
            f"            if not {N['hs']}:raise",
            f"            {y}={N['hs']}.pop();del {s}[{y}[0]:];{s}.append({_e});{k}={y}[1]",
        ]
        L.insert(_wi + 1, "        try:")
    return "\n".join(x2 for x2 in L if x2.strip()) + "\n"
def compile_function_to_vm(func: ast.FunctionDef) -> Program:
    work = copy.deepcopy(func)
    ir_prog = ASTToIR().lower_function(work)
    ir_prog = ConstFoldPass().run(ir_prog)
    return IRToVM().lower(ir_prog)
def _is_entry_wrapper(fn: ast.FunctionDef) -> bool:
    b = fn.body
    return (len(b) >= 2
            and isinstance(b[-1], ast.Expr)
            and isinstance(b[-1].value, ast.Call)
            and isinstance(b[-1].value.func, ast.Name)
            and b[-1].value.func.id == fn.name
            and is_generated(b[-1]))
def _is_entry_wrapped_tree(tree: ast.Module) -> Optional[ast.FunctionDef]:
    if (len(tree.body) == 2
            and isinstance(tree.body[0], ast.FunctionDef)
            and isinstance(tree.body[1], ast.Expr)
            and isinstance(tree.body[1].value, ast.Call)
            and isinstance(tree.body[1].value.func, ast.Name)
            and tree.body[1].value.func.id == tree.body[0].name
            and is_generated(tree.body[1])):
        return tree.body[0]
    return None
def _vm_candidate_functions(tree: ast.Module):
    _entry = _is_entry_wrapped_tree(tree)
    if _entry is not None:
        for stmt in _entry.body:
            if isinstance(stmt, ast.FunctionDef):
                yield stmt
            elif isinstance(stmt, ast.AsyncFunctionDef):
                continue
            elif isinstance(stmt, ast.ClassDef):
                for sub in stmt.body:
                    if isinstance(sub, ast.FunctionDef):
                        yield sub
        return
    def _rec(node, in_function: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                continue
            if isinstance(child, ast.FunctionDef):
                if not in_function:
                    yield child
                    _rec(child, not _is_entry_wrapper(child))
                else:
                    _rec(child, True)
            elif isinstance(child, (ast.ClassDef, ast.Lambda)):
                _rec(child, in_function if isinstance(child, ast.ClassDef) else True)
            else:
                _rec(child, in_function)
    yield from _rec(tree, False)
def _is_vm_eligible(func: ast.FunctionDef) -> bool:
    if any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)) for n in ast.walk(func)):
        return False
    return True
def apply_selective_vm_virtualization(tree: ast.Module, build_seed: int,
                                       force_flat: bool = False,
                                       scrub: Optional[List[str]] = None,
                                       ) -> Tuple[int, int, int, int, List[str], Dict[str, int]]:
    regions = 0
    total_instructions = 0
    virtualized_stmts = 0
    fallback_count = 0
    warnings: List[str] = []
    helper_name = _hira_token(build_seed, "vm_exec", 10)
    pool_name = _hira_token(build_seed, "vm_pool", 10)
    perm = _permute_opcodes(build_seed)
    operand_mask = int.from_bytes(
        hashlib.sha256(f"{build_seed}::vm_operand_mask".encode()).digest()[:4], "big")
    consts: List[Any] = []
    cidx: Dict[Tuple[str, Any], int] = {}
    if force_flat:
        dispatch_mode = "flat"
    else:
        dispatch_mode = ("flat", "grouped", "nested")[derive_seed(build_seed, "vm_dispatch_mode") % 3]
    candidates: List[Tuple[int, ast.FunctionDef]] = []
    for node in _vm_candidate_functions(tree):
        if not _is_vm_eligible(node):
            continue
        score = sum(1 for _ in ast.walk(node))
        candidates.append((score, node))
    candidates.sort(key=lambda t: (-t[0], t[1].name))
    max_regions = 512
    compiled: List[Tuple[ast.FunctionDef, Program, List[str], int]] = []
    for score, node in candidates[:max_regions]:
        stmt_count = len(node.body)
        try:
            program = compile_function_to_vm(node)
        except (VMCompileError, IRBuildError) as e:
            fallback_count += 1
            warnings.append(f"[SKIPPED] VM virtualization for '{node.name}': {e}")
            continue
        params = ([a.arg for a in node.args.posonlyargs]
                  + [a.arg for a in node.args.args]
                  + [a.arg for a in node.args.kwonlyargs])
        if node.args.vararg:
            params.append(node.args.vararg.arg)
        if node.args.kwarg:
            params.append(node.args.kwarg.arg)
        compiled.append((node, program, params, stmt_count))
    stats: Dict[str, int] = {"const_pool": 0, "serialized_words": 0, "used_opcodes": 0}
    if not compiled:
        return 0, 0, 0, fallback_count, warnings, stats
    used_union: Set[Opcode] = set()
    serialized: List[Tuple[ast.FunctionDef, Tuple[int, ...], Dict[str, int], List[str], int, Set[str]]] = []
    all_pair_idxs: Set[int] = set()
    for node, program, params, stmt_count in compiled:
        _rmask = _vm_region_mask(build_seed, node.name, operand_mask)
        flat, slots, used, pair_idxs = _serialize_vm_program(
            program, perm, consts, cidx, operand_mask=_rmask)
        used_union |= used
        all_pair_idxs.update(pair_idxs)
        total_instructions += len(program.instructions)
        _gset = {ins.operand for ins in program.instructions
                 if ins.opcode in (Opcode.LOAD_VAR_G, Opcode.STORE_ATTR,
                                   Opcode.STORE_GLOBAL, Opcode.DELETE_GLOBAL,
                                   Opcode.DELETE_NAME)
                 and isinstance(ins.operand, str)}
        serialized.append((node, flat, slots, params, stmt_count, _gset,
                             _rmask))
    if consts:
        pc_id = perm[Opcode.LOAD_CONST]
        cslot_opids = {pc_id, perm[Opcode.CALL_FUNC_KW],
                       perm[Opcode.IMPORT_NAME], perm[Opcode.CONST_STORE]}
        rng_pool = _seeded_rng(build_seed, "vm_pool_shuffle")
        pool_order = list(range(len(consts)))
        rng_pool.shuffle(pool_order)
        const_remap = {old: new for new, old in enumerate(pool_order)}
        for _pi in all_pair_idxs:
            _c, _s = consts[_pi]
            consts[_pi] = (const_remap[_c], _s)
        remapped_serialized = []
        for node, flat, slots, params, stmt_count, _gset, _rm in serialized:
            fl = list(flat)
            for i in range(0, len(fl), 2):
                if fl[i] in cslot_opids:
                    _poskey = _rm ^ ((i // 2) & 255)
                    old_idx = fl[i + 1] ^ _poskey
                    fl[i + 1] = const_remap[old_idx] ^ _poskey
            remapped_serialized.append(
                (node, tuple(fl), slots, params, stmt_count, _gset, _rm))
        serialized = remapped_serialized
        consts_ordered = [consts[old] for old in pool_order]
    else:
        consts_ordered = list(consts)
    prelude_nodes: List[ast.stmt] = []
    table_reprs: List[str] = []
    ntab_reprs: List[str] = []
    shuffle_rng = _seeded_rng(build_seed, "vm_handler_order")
    try:
        helper_src = _vm_runtime_helper_source(helper_name, perm, used_union,
                                                operand_mask=operand_mask,
                                                shuffle_rng=shuffle_rng,
                                                build_seed=build_seed,
                                                dispatch_mode=dispatch_mode)
        ast.parse(helper_src)
    except Exception as _herr:
        warnings.append(f"[SKIPPED] VM helper emission failed ({_herr!r}); "
                        f"build continues WITHOUT VM.")
        return (0, total_instructions, 0, fallback_count + len(compiled),
                warnings, {"const_pool": 0, "serialized_words": 0,
                           "used_opcodes": 0, "dispatch_mode": dispatch_mode})
    for idx, (node, flat, slots, params, stmt_count, _gset, _rm) in enumerate(serialized):
        tbl_name = f"{pool_name}と{idx}"
        ntab_name = f"{pool_name}ぬ{idx}"
        for _pp in params:
            if _pp not in slots:
                slots[_pp] = len(slots)
        _full_ordered = sorted(slots, key=slots.get)
        ntuple = tuple(n if n in _gset else "" for n in _full_ordered)
        table_reprs.append(repr(flat))
        ntab_reprs.append(repr(ntuple))
        tbl_node = ast.parse(f"{tbl_name} = {flat!r}").body[0]
        mark_generated(tbl_node)
        prelude_nodes.append(tbl_node)
        ntab_node = ast.parse(f"{ntab_name} = {ntuple!r}").body[0]
        mark_generated(ntab_node)
        prelude_nodes.append(ntab_node)
        pairs = ", ".join(f"{slots[p]}: {p}" for p in params)
        new_body: ast.stmt = ast.parse(
            f"return {helper_name}({tbl_name}, {pool_name}, {ntab_name}, {{{pairs}}}, {_rm})"
        ).body[0]
        ast.copy_location(new_body, node)
        mark_generated(new_body)
        node.body = [new_body]
        ast.fix_missing_locations(node)
        regions += 1
        virtualized_stmts += stmt_count
    masked_pool = _mask_vm_consts(consts_ordered, operand_mask)
    pool_node = ast.parse(f"{pool_name} = {masked_pool!r}").body[0]
    mark_generated(pool_node)
    prelude_nodes.append(pool_node)
    pool_repr = repr(masked_pool)
    expected_crc = zlib.crc32(
        pool_repr.encode("utf-8")
        + "".join(tr for tr in table_reprs).encode("utf-8")
        + "".join(nr for nr in ntab_reprs).encode("utf-8"))
    _chk_var = _hira_token(build_seed, "vm_crc_tmp", 6)
    _zlib_alias = _hira_token(build_seed, "vm_zlib_alias", 5)
    tbl_refs = ", ".join(f"{pool_name}と{i}" for i in range(len(serialized)))
    ntab_refs = ", ".join(f"{pool_name}ぬ{i}" for i in range(len(serialized)))
    _h_zlib = _hidden_name_src("zlib", build_seed, "hidmod")
    chk_src = (
        f"{_zlib_alias}=__import__({_h_zlib})\n"
        f"{_chk_var}と = ({tbl_refs},)\n"
        f"{_chk_var}ぬ = ({ntab_refs},)\n"
        f"if {_zlib_alias}.crc32(repr({pool_name}).encode() + b''.join("
        f"repr(かず).encode() for かず in {_chk_var}と)"
        f" + b''.join(repr(かず).encode() for かず in {_chk_var}ぬ)"
        f") != {expected_crc}:\n"
        f"    raise RuntimeError('Dkh runtime fault')\n"
    )
    for st in ast.parse(chk_src).body:
        mark_generated(st)
        prelude_nodes.append(st)
    helper_node = ast.parse(helper_src).body[0]
    mark_generated(helper_node)
    tree.body[_module_insert_index(tree):_module_insert_index(tree)] = (
        [helper_node] + prelude_nodes)
    ast.fix_missing_locations(tree)
    if scrub is not None:
        scrub.append(helper_name)
        scrub.append(pool_name)
        for i in range(len(serialized)):
            scrub.append(f"{pool_name}と{i}")
            scrub.append(f"{pool_name}ぬ{i}")
        scrub.append(_chk_var)
        scrub.append(f"{_chk_var}と")
        scrub.append(f"{_chk_var}ぬ")
        scrub.append(_zlib_alias)
    stats = {
        "const_pool": len(consts),
        "serialized_words": sum(len(f) for _, f, _, _, _, _, _ in serialized),
        "used_opcodes": len(used_union),
        "dispatch_mode": dispatch_mode,
    }
    return (regions, total_instructions, virtualized_stmts,
            fallback_count, warnings, stats)
@dataclass
class ValidationResult:
    parsed: bool = False
    compiled: bool = False
    executed: Optional[bool] = None
    behavior_match: Optional[bool] = None
    error_stage: Optional[str] = None
    error_message: Optional[str] = None
    @property
    def success(self) -> bool:
        if not (self.parsed and self.compiled):
            return False
        if self.executed is False:
            return False
        if self.behavior_match is False:
            return False
        return True
    def status_line(self) -> str:
        if self.success:
            return "[SUCCESS]"
        return f"[VALIDATION FAILED]\nStage: {self.error_stage}\nReason: {self.error_message}"
def execute_output_for_validation(output_source: str, timeout: float = 30.0
                                   ) -> Tuple[bool, str]:
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False,
                                          encoding="utf-8") as f:
            f.write(output_source)
            tmp_path = f.name
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-6:]
            return False, f"exited with code {proc.returncode}: " + " | ".join(tail)
        return True, "executed successfully (exit code 0)"
    except subprocess.TimeoutExpired:
        return False, f"execution exceeded {timeout}s timeout"
    except Exception as e:
        return False, f"validation subprocess error: {e!r}"
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
def validate_source(
    source: str,
    filename: str = "<dzydkh_output>",
    run: bool = False,
    exec_globals: Optional[Dict[str, Any]] = None,
    behavior_check: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> ValidationResult:
    result = ValidationResult()
    try:
        tree = ast.parse(source, filename=filename)
        result.parsed = True
    except SyntaxError as e:
        result.error_stage = "PARSE"
        result.error_message = str(e)
        return result
    try:
        code_obj = compile(tree, filename=filename, mode="exec")
        result.compiled = True
    except Exception as e:
        result.error_stage = "COMPILE"
        result.error_message = str(e)
        return result
    if run:
        namespace = exec_globals if exec_globals is not None else {}
        try:
            exec(code_obj, namespace)
            result.executed = True
        except Exception:
            result.executed = False
            result.error_stage = "EXECUTE"
            result.error_message = traceback.format_exc()
            return result
        if behavior_check is not None:
            try:
                result.behavior_match = behavior_check(namespace)
                if not result.behavior_match:
                    result.error_stage = "BEHAVIOR"
                    result.error_message = "Output behavior did not match expected result."
            except Exception:
                result.behavior_match = False
                result.error_stage = "BEHAVIOR"
                result.error_message = traceback.format_exc()
    return result
def _integrity_boundary_names(build_seed: int) -> str:
    return _hira_token(build_seed, "seal", 10)
def _header_field_values(username: str,
                         target_version: Tuple[int, int]) -> Tuple[str, str, int, int]:
    major, minor = target_version
    return (repr(str(username)), time.strftime("%Y-%m-%d - %H-%M-%S"),
            major, minor)
_WARNING_FULL = (
    "__WARNING__ = {\n"
    "\"ENG\": \"This obfuscator is made to protect people's code from being stolen, cracked. "
    "So there will be many bad elements using this obfuscator to obfuscate botnet files, "
    "keylogs, etc. So be careful when running this file!\",\n"
    "\"VIE\": \"Obfuscator này được làm ra để bảo vệ code của mọi người tránh bị đánh cắp, crack. "
    "Vì vậy sẽ có nhiều thành phần xấu sử dụng obfuscator này để obfuscate những file botnet, "
    "keylog, v.v. Vì vậy hãy cẩn thận khi run file này!\"\n"
    "}\n"
)
_WARNING_COMPACT = ("__WARNING__={'ENG':'Protect code from being stolen, cracked.',"
                    "'VIE':'Bao ve code tranh bi danh cap, crack.'}")
def _version_guard_source(message: str, major: int, minor: int,
                          build_seed: int = 0, compact: bool = False) -> str:
    _h_sys = _hidden_name_src("sys", build_seed, "hidmod")
    _h_exit = _hidden_name_src("exit", build_seed, "hidattr")
    _h_vermsg = _hidden_name_src(message, build_seed, "hidmsg")
    _vg_rng = _seeded_rng(build_seed, "shape::verguard")
    _chk = f"__import__({_h_sys}).version_info[:2]"
    _pick = _vg_rng.random()
    if _pick < 0.25:
        _head = (f"if {_chk}!=({major},{minor}):" if compact
                 else f"if {_chk} != ({major}, {minor}): ")
    elif _pick < 0.5:
        _head = (f"if not({_chk}==({major},{minor})):" if compact
                 else f"if not ({_chk} == ({major}, {minor})): ")
    elif _pick < 0.75:
        _head = (f"if {_chk}<({major},{minor})or {_chk}>({major},{minor}):" if compact
                 else f"if {_chk} < ({major}, {minor}) or {_chk} > ({major}, {minor}): ")
    else:
        _head = (f"if({major},{minor})!=({_chk}):" if compact
                 else f"if ({major}, {minor}) != ({_chk}): ")
    return (_head
            + f"getattr(__import__({_h_sys}), {_h_exit})({_h_vermsg})\n")
def _python_version_guard_source(target_version: Tuple[int, int], build_seed: int = 0) -> str:
    major, minor = target_version
    return _version_guard_source(
        f"DkhObfuscate requires Python {major}.{minor}",
        major, minor, build_seed, compact=False)
def _header_source(username: str, target_version: Tuple[int, int]) -> str:
    safe_username_literal, obf_time, major, minor = _header_field_values(
        username, target_version)
    return (
        "#!/bin/python3.13\n"
        "# -*- coding: utf-8 -*-\n"
        "__OWNER__ = \"Dkh\"\n"
        "__OBFUSCATOR__ = \"Dkh\"\n"
        f"__USERNAME__ = {safe_username_literal}\n"
        f"__OBF_TIME__ = {obf_time!r}\n"
        f"__PYTHON__ = \"{major}.{minor}\"\n"
        + _WARNING_FULL
        + "\n"
        "# Obfuscated By Dkh: https://github.com/DzyKhdra/DkhObfuscator\n"
        "# Website : dkhang.pages.dev\n"
        "\n"
    )
def build_header_and_guard(username: str, target_version: Tuple[int, int], build_seed: int = 0) -> str:
    return _header_source(username, target_version) + _python_version_guard_source(target_version, build_seed)
def _compact_header_and_guard(username: str, target_version: Tuple[int, int], build_seed: int = 0) -> str:
    safe_username_literal, obf_time, major, minor = _header_field_values(
        username, target_version)
    return (
        "#!/bin/python3.13\n"
        "# -*- coding: utf-8 -*-\n"
        f"__OWNER__=\"Dkh\";__OBFUSCATOR__=\"Dkh\";"
        f"__USERNAME__={safe_username_literal};__OBF_TIME__={obf_time!r};"
        f"__PYTHON__=\"{major}.{minor}\";{_WARNING_COMPACT}\n"
        "# Obfuscated By Dkh: https://github.com/DzyKhdra/DkhObfuscator\n"
        "# Website : dkhang.pages.dev\n"
        + _version_guard_source(
            f"DkhObfuscate requires Python {major}.{minor}",
            major, minor, build_seed, compact=True)
    )
def _pack_compact_lines(source: str) -> str:
    lines = source.splitlines()
    cleaned: List[str] = []
    for ln in lines:
        if not ln.strip():
            continue
        cleaned.append(ln.rstrip())
    def _depths(ls: List[str]) -> List[Tuple[int, int]]:
        res: List[Tuple[int, int]] = []
        depth = 0
        instr: Optional[str] = None
        for ln in ls:
            start = depth
            i, n = 0, len(ln)
            while i < n:
                if instr is not None:
                    if ln.startswith(instr, i):
                        if len(instr) == 1:
                            bs = 0
                            j = i - 1
                            while j >= 0 and ln[j] == "\\":
                                bs += 1
                                j -= 1
                            if bs % 2 == 1:
                                i += 1
                                continue
                        i += len(instr)
                        instr = None
                    else:
                        i += 1
                    continue
                ch = ln[i]
                if ch == "#":
                    break
                if ch in ('"', "'"):
                    if ln.startswith(ch * 3, i):
                        instr = ch * 3
                        i += 3
                    else:
                        instr = ch
                        i += 1
                    continue
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth = max(0, depth - 1)
                i += 1
            res.append((start, depth))
        return res
    _dep = _depths(cleaned)
    _COMPOUND = ("def ", "if ", "elif ", "else:", "else ", "for ", "while ",
                 "try:", "except", "finally:", "with ", "class ", "match ",
                 "case ")
    merged: List[str] = []
    merged_dep: List[Tuple[int, int]] = []
    i = 0
    while i < len(cleaned):
        ln = cleaned[i]
        stripped = ln.strip()
        if (stripped.startswith("def ") and stripped.endswith(":")
                and i + 1 < len(cleaned)
                and _dep[i] == (0, 0) and _dep[i + 1] == (0, 0)):
            nxt = cleaned[i + 1]
            nstr = nxt.strip()
            cur_ind = len(ln) - len(ln.lstrip())
            nxt_ind = len(nxt) - len(nxt.lstrip())
            _after = cleaned[i + 2] if i + 2 < len(cleaned) else ""
            _after_ind = (len(_after) - len(_after.lstrip())) if _after.strip() else cur_ind
            _single_body = (not _after.strip()) or (_after_ind <= cur_ind)
            if (_single_body and nxt_ind > cur_ind and not nstr.endswith(":")
                    and not nstr.startswith(_COMPOUND)
                    and nstr not in ("else:", "try:", "finally:")
                    and nxt.count("(") == nxt.count(")")
                    and nxt.count("[") == nxt.count("]")
                    and nxt.count("{") == nxt.count("}")):
                merged.append((" " * cur_ind) + ln.strip() + nstr)
                merged_dep.append((0, 0))
                i += 2
                continue
        merged.append(ln)
        merged_dep.append(_dep[i])
        i += 1
    out: List[str] = []
    buf: List[str] = []
    buf_ind: Optional[int] = None
    def _merged_ok(k: int) -> bool:
        try:
            return merged_dep[k] == (0, 0)
        except IndexError:
            return False
    def _is_simple(ln: str) -> bool:
        s = ln.strip()
        if not s or s.startswith("#"):
            return False
        if s.endswith(":"):
            return False
        for kw in _COMPOUND:
            if s.startswith(kw):
                return False
        if s.startswith("@"):
            return False
        if not (ln.count("(") == ln.count(")")
                and ln.count("[") == ln.count("]")
                and ln.count("{") == ln.count("}")):
            return False
        return True
    def _flush():
        nonlocal buf, buf_ind
        if not buf:
            return
        if len(buf) == 1:
            out.append(buf[0])
        else:
            ind = " " * (buf_ind or 0)
            joined = ";".join(b.strip() for b in buf)
            out.append(f"{ind}{joined}")
        buf = []
        buf_ind = None
    for _mi, ln in enumerate(merged):
        ind = len(ln) - len(ln.lstrip())
        if _merged_ok(_mi) and _is_simple(ln) and (not buf or ind == buf_ind):
            if not buf:
                buf_ind = ind
            buf.append(ln)
        else:
            _flush()
            out.append(ln)
    _flush()
    return "\n".join(out) + "\n"
def _pack_preserving_ast(text: str) -> Tuple[str, bool]:
    try:
        pre = ast.dump(ast.parse(text))
    except Exception:
        return text, False
    try:
        packed = _pack_compact_lines(text)
        if ast.dump(ast.parse(packed)) == pre:
            return packed, True
    except Exception:
        pass
    return text, False
def _compute_body_checksum(body_source: str) -> str:
    return hashlib.sha256(body_source.encode("utf-8")).hexdigest()
def _tidy_blank_lines(text: str) -> str:
    import re as _re
    tidied = _re.sub(r"\n{3,}", "\n\n", text)
    if not tidied.endswith("\n"):
        tidied += "\n"
    return tidied
def _tamper_func_name(build_seed: int) -> str:
    return _hira_token(build_seed, "tamper_retaliation", 8)
def _tamper_response_source(tamper_url: str, build_seed: int = 0) -> str:
    fn = _tamper_func_name(build_seed)
    return (
        f"def {fn}():\n"
        f"    try:\n"
        f"        _w = __import__('webbrowser')\n"
        f"        _t = __import__('time')\n"
        f"        for _i in range(20):\n"
        f"            try:\n"
        f"                _w.open_new_tab({tamper_url!r})\n"
        f"            except Exception:\n"
        f"                break\n"
        f"            _t.sleep(0.5)\n"
        f"    except Exception:\n"
        f"        pass\n"
        f"    try:\n"
        f"        _o = __import__('os')\n"
        f"        _p = globals().get('__file__') or ''\n"
        f"        if _p:\n"
        f"            _o.remove(_p)\n"
        f"    except Exception:\n"
        f"        pass\n")
def _integrity_verifier_source(enable_tamper: bool, seal_name: str, build_seed: int = 0, site: str = "", tamper_func: Optional[str] = None) -> str:
    fail = ""
    if enable_tamper and tamper_func:
        fail = f"{tamper_func}(); "
    _h_hl = _hidden_name_src("hashlib", build_seed, "hidmod")
    _h_sy2 = _hidden_name_src("sys", build_seed, "hidmod")
    _h_s256 = _hidden_name_src("sha256", build_seed, "hidattr")
    _h_hexd = _hidden_name_src("hexdigest", build_seed, "hidattr")
    _h_se = _hidden_name_src("stderr", build_seed, "hidattr")
    _h_wr = _hidden_name_src("write", build_seed, "hidattr")
    _h_ex = _hidden_name_src("exit", build_seed, "hidattr")
    _h_msg1 = _hidden_name_src(
        f"Dkh integrity check failed [{_envelope_poly(build_seed)['err_code']}/schema",
        build_seed, "hidmsg")
    _h_msg2 = _hidden_name_src("]", build_seed, "hidmsg")
    _h_msg3 = _hidden_name_src(" See ", build_seed, "hidmsg")
    _h_nl = _hidden_name_src("\n", build_seed, "hidmsg")
    _site_expr = f"({_h_msg3} + {site!r})" if site else "''"
    _tail_rng = _seeded_rng(build_seed, "shape::verifier_tail")
    _tail = ("except OSError: pass\n" if _tail_rng.random() < 0.5
             else "except (OSError, IOError): pass\n")
    _terms = [
        "先 < 0",
        "行末 <= 0",
        f"本文[先:行末] != {seal_name!r} + ' = ' + repr({seal_name}) + '\\n'",
        f"getattr(getattr(はせ, {_h_s256})(本文[:先].encode('utf-8')), {_h_hexd})() != 期頭",
        f"getattr(getattr(はせ, {_h_s256})(本文[行末:].encode('utf-8')), {_h_hexd})() != 期末",
    ]
    _seeded_rng(build_seed, "shape::verifier_terms").shuffle(_terms)
    return (
        "try:\n"
        "    しせ = __import__(" + _h_sy2 + "); はせ = __import__(" + _h_hl + "); 本文 = open("
        "globals().get('__file__') or '', encoding='utf-8').read()\n"
        f"    先 = 本文.find({seal_name + ' = ('!r}); 行末 = 本文.find('\\n', 先) + 1;"
        f" 期頭, 期末 = {seal_name}\n"
        f"    if {' or '.join(_terms)}: "
        f"getattr(getattr(しせ, {_h_se}), {_h_wr})({_h_msg1} + str({INTEGRITY_SCHEMA_VERSION}) + {_h_msg2} + {_site_expr} + {_h_nl}); " + fail + f"getattr(しせ, {_h_ex})(1)\n"
        + _tail
    )
def _guard_token_value() -> str:
    return hashlib.sha256(
        "guard-ok::exec,eval,compile,__import__".encode()).hexdigest()[:16]
@functools.lru_cache(maxsize=128)
def _envelope_poly(build_seed: int) -> Dict[str, Any]:
    prng = _seeded_rng(build_seed, "envelope::poly")
    mrng = _seeded_rng(build_seed, "envelope::markers")
    _alpha = "abcdefghijklmnopqrstuvwxyz0123456789"
    _guard_tag = "".join(mrng.choice(_alpha)
                         for _ in range(mrng.randint(8, 12)))
    _guard_order = ["exec", "eval", "compile", "__import__"]
    _seeded_rng(build_seed, "envelope::guardorder").shuffle(_guard_order)
    _preimage = _guard_tag + "::" + ",".join(_guard_order)
    _token_hex = hashlib.sha256(_preimage.encode()).hexdigest()[:16]
    _dc_alpha = "abcdefghijklmnopqrstuvwxyz0123456789_"
    _dc_tag = "".join(mrng.choice(_dc_alpha)
                      for _ in range(mrng.randint(6, 12)))
    _ncut = mrng.choice([1, 2])
    _cuts = sorted(mrng.sample(range(1, len(_dc_tag)), k=_ncut))
    _parts, _prev = [], 0
    for _c in _cuts + [len(_dc_tag)]:
        _parts.append(_dc_tag[_prev:_c])
        _prev = _c
    return {
        "guard_tag": _guard_tag, "guard_order": _guard_order,
        "guard_preimage": _preimage, "token_hex": _token_hex,
        "token_int": int(_token_hex[:2], 16),
        "dc_tag": _dc_tag, "dc_parts": _parts,
        "n_buckets": prng.randint(7, 12),
        "order": prng.choice(["OUT", "IN", "SEQ"]),
        "comp": prng.choice(["zlib", "bz2"]),
        "mask_id": prng.randint(0, 4),
        "seed_shape": prng.randint(0, 3),
        "err_code": f"ZD-E{mrng.randint(100, 999)}",
    }
def _comp_level_for(poly_comp: str, level: int) -> int:
    level = int(level)
    if poly_comp == "bz2":
        return max(1, min(9, level if level > 0 else 1))
    return max(0, min(9, level))
def _compress_for_poly(data: bytes, poly: Dict[str, Any], level: int) -> bytes:
    import bz2 as _bz2
    if poly.get("comp") == "bz2":
        return _bz2.compress(data, compresslevel=_comp_level_for("bz2", level))
    return zlib.compress(data, _comp_level_for("zlib", level))
@functools.lru_cache(maxsize=128)
def _canary_pair(build_seed: int, comp: str) -> Tuple[bytes, bytes]:
    plain = hashlib.sha256(f"{build_seed}::canary".encode()).digest()[:8]
    return plain, _compress_for_poly(plain, {"comp": comp}, 9)
def _poly_seed_expr(seed_shape: int, s1: int, s2: int,
                    s3: int) -> Tuple[str, int]:
    if seed_shape == 1:
        return f"({s1}+{s2}-{s3})", s1 + s2 - s3
    if seed_shape == 2:
        return f"(({s1}*3+{s2})^{s3})", ((s1 * 3 + s2) ^ s3)
    if seed_shape == 3:
        return f"(({s1}*7+{s2}*3+{s3})&16777215)", ((s1 * 7 + s2 * 3 + s3) & 16777215)
    return f"({s1}^{s2}^{s3})", s1 ^ s2 ^ s3
def _poly_mask_enc(mask_id: int, b: int, i: int, ln: int, t: int) -> int:
    if mask_id == 1:
        return b ^ ((i * 67 + ln * 3 + t * 7) & 255)
    if mask_id == 2:
        return (b + i * 31 + ln + t) & 255
    if mask_id == 3:
        return b ^ ((i * 131 + ln * 7 + t * 13 + (i & ln)) & 255)
    if mask_id == 4:
        return (b + i * i + ln * 5 + t * 11) & 255
    return b ^ ((i * 31 + ln + t) & 255)
def _poly_mask_dec_src(mask_id: int, b_var: str, i_var: str,
                       len_expr: str, t_expr: str) -> str:
    if mask_id == 1:
        return (f"({b_var}^(({i_var}*67+{len_expr}*3+{t_expr}*7)&255))")
    if mask_id == 2:
        return (f"(({b_var}-{i_var}*31-{len_expr}-{t_expr})&255)")
    if mask_id == 3:
        return (f"({b_var}^(({i_var}*131+{len_expr}*7+{t_expr}*13+({i_var}&{len_expr}))&255))")
    if mask_id == 4:
        return (f"(({b_var}-{i_var}*{i_var}-{len_expr}*5-{t_expr}*11)&255)")
    return f"({b_var}^(({i_var}*31+{len_expr}+{t_expr})&255))"
def _poly_dc_expr(dc_parts: List[str]) -> str:
    return " + ".join([repr("::")] + [repr(p) for p in dc_parts]
                      + [repr("::")])
def _anti_analysis_source(func_name: str, build_seed: int = 0,
                           half: str = "full") -> str:
    _ps = "" if half == "full" else f":{half}"
    _h_bi = _hidden_name_src("builtins", build_seed, f"hidmod{_ps}")
    _h_ex = _hidden_name_src("exec", build_seed, f"hidattr{_ps}")
    _h_ev = _hidden_name_src("eval", build_seed, f"hidattr{_ps}")
    _h_co = _hidden_name_src("compile", build_seed, f"hidattr{_ps}")
    _h_im = _hidden_name_src("__import__", build_seed, f"hidattr{_ps}")
    _h_mo = _hidden_name_src("monitoring", build_seed, f"hidattr{_ps}")
    _h_gt = _hidden_name_src("gettrace", build_seed, f"hidattr{_ps}")
    _h_gp = _hidden_name_src("getprofile", build_seed, f"hidattr{_ps}")
    _h_sy = _hidden_name_src("sys", build_seed, f"hidmod{_ps}")
    _h_xx = _hidden_name_src("exit", build_seed, f"hidattr{_ps}")
    _h_fh = _hidden_name_src("faulthandler", build_seed, f"hidmod{_ps}")
    _h_fe = _hidden_name_src("is_enabled", build_seed, f"hidattr{_ps}")
    _h_md = _hidden_name_src("modules", build_seed, f"hidattr{_ps}")
    _h_os = _hidden_name_src("os", build_seed, f"hidmod{_ps}")
    _h_ev2 = _hidden_name_src("environ", build_seed, f"hidattr{_ps}")
    _g_f = _hira_token(build_seed, f"antiana{_ps}::fh", 6)
    _g_o = _hira_token(build_seed, f"antiana{_ps}::os", 6)
    _g_s = _hira_token(build_seed, f"antiana{_ps}::sys", 6)
    _g_b = _hira_token(build_seed, f"antiana{_ps}::blt", 6)
    _g_m = _hira_token(build_seed, f"antiana{_ps}::mon", 6)
    _g_e = _hira_token(build_seed, f"antiana{_ps}::env", 6)
    _h_hl0 = _hidden_name_src("hashlib", build_seed, f"hidmod{_ps}")
    _h_sha0 = _hidden_name_src("sha256", build_seed, f"hidattr{_ps}")
    _h_hex0 = _hidden_name_src("hexdigest", build_seed, f"hidattr{_ps}")
    _poly_g = _envelope_poly(build_seed)
    _gok = _hidden_name_src(_poly_g["guard_tag"] + "::", build_seed,
                            f"hidmsg{_ps}")
    _g_ord = {"exec": _h_ex, "eval": _h_ev, "compile": _h_co,
              "__import__": _h_im}
    _g_tup = "(" + ", ".join(_g_ord[k] for k in _poly_g["guard_order"]) + ",)"
    _ret = (f"    return getattr(getattr(__import__({_h_hl0}), {_h_sha0})"
            f"(({_gok} + ','.join({_g_tup})).encode()), {_h_hex0})()[:16]\n")
    _head = (f"def {func_name}():\n"
             f"    {_g_s} = __import__({_h_sy})\n"
             f"    {_g_e} = ({_h_ex}, {_h_ev}, {_h_co}, {_h_im})\n")
    _trace = (f"    {_g_m} = getattr({_g_s}, {_h_mo}, None)\n"
              f"    if getattr({_g_s}, {_h_gt})() is not None or getattr({_g_s}, {_h_gp}, lambda: None)() or ({_g_m} and any({_g_m}.get_tool(i) for i in (1, 2, 3))): getattr({_g_s}, {_h_xx})(0)\n"
              f"    {_g_f} = __import__({_h_fh})\n"
              f"    if getattr({_g_f}, {_h_fe}, lambda: False)(): getattr({_g_s}, {_h_xx})(0)\n"
              f"    if 'pdb' in getattr({_g_s}, {_h_md}) or 'bdb' in getattr({_g_s}, {_h_md}) or 'pydevd' in getattr({_g_s}, {_h_md}): getattr({_g_s}, {_h_xx})(0)\n")
    _hook = (f"    {_g_b} = __import__({_h_bi})\n"
             f"    if any(getattr({_g_b}, n, None) is None or type(getattr({_g_b}, n)).__name__ != 'builtin_function_or_method' for n in {_g_e}) or __import__({_h_bi}) is not {_g_b}: getattr({_g_s}, {_h_xx})(0)\n"
             f"    {_g_o} = __import__({_h_os})\n"
             f"    if any(k in getattr({_g_o}, {_h_ev2}, {{}}) for k in ('PYTHONBREAKPOINT', 'PYTHONVERBOSE', 'PYTHONINSPECT', 'PYTHONDEBUG')): getattr({_g_s}, {_h_xx})(0)\n")
    if half == "trace":
        return _head + _trace + _ret
    if half == "hook":
        return _head + _hook + _ret
    return _head + _trace + _hook + _ret
def _extra_guard_source(func_name: str, build_seed: int = 0) -> str:
    _ps = ":extra"
    _h_bi = _hidden_name_src("builtins", build_seed, f"hidmod{_ps}")
    _h_op = _hidden_name_src("open", build_seed, f"hidattr{_ps}")
    _h_os = _hidden_name_src("os", build_seed, f"hidmod{_ps}")
    _h_sy = _hidden_name_src("system", build_seed, f"hidattr{_ps}")
    _h_yy = _hidden_name_src("sys", build_seed, f"hidmod{_ps}")
    _h_xx = _hidden_name_src("exit", build_seed, f"hidattr{_ps}")
    _g_b = _hira_token(build_seed, "antiextra::blt", 6)
    _g_o = _hira_token(build_seed, "antiextra::os", 6)
    _g_s = _hira_token(build_seed, "antiextra::sys", 6)
    return (f"def {func_name}():\n"
            f"    {_g_s} = __import__({_h_yy})\n"
            f"    {_g_b} = __import__({_h_bi})\n"
            f"    if type(getattr({_g_b}, {_h_op}, None)).__name__ != 'builtin_function_or_method': getattr({_g_s}, {_h_xx})(0)\n"
            f"    {_g_o} = __import__({_h_os})\n"
            f"    if type(getattr({_g_o}, {_h_sy}, None)).__name__ != 'builtin_function_or_method': getattr({_g_s}, {_h_xx})(0)\n")
def _watchdog_source(wd_func: str, build_seed: int) -> Tuple[str, str]:
    iv = 4 + (derive_seed(build_seed, "watchdog") % 4)
    H = lambda n, p: _hidden_name_src(n, build_seed, p)
    _h_sy = H("sys", "hidmod:wd")
    _h_bi = H("builtins", "hidmod:wd")
    _h_os = H("os", "hidmod:wd")
    _h_tm = H("time", "hidmod:wd")
    _h_th = H("threading", "hidmod:wd")
    _h_sl = H("sleep", "hidattr:wd")
    _h_pc = H("perf_counter", "hidattr:wd")
    _h_gt = H("gettrace", "hidattr:wd")
    _h_gp = H("getprofile", "hidattr:wd")
    _h_md = H("modules", "hidattr:wd")
    _h_mo = H("monitoring", "hidmod:wd")
    _h_ex = H("_exit", "hidattr:wd")
    _h_x1 = H("exec", "hidattr:wd")
    _h_x2 = H("eval", "hidattr:wd")
    _h_x3 = H("compile", "hidattr:wd")
    _h_x4 = H("__import__", "hidattr:wd")
    _h_Th = H("Thread", "hidattr:wd")
    _h_st = H("start", "hidattr:wd")
    _w_s = _hira_token(build_seed, "watchdog::sys", 6)
    _w_t = _hira_token(build_seed, "watchdog::time", 6)
    _w_t0 = _hira_token(build_seed, "watchdog::t0", 6)
    _w_m = _hira_token(build_seed, "watchdog::mon", 6)
    _w_thr = f"getattr(__import__({_h_th}), {_h_Th})"
    _loop_rng = _seeded_rng(build_seed, "watchdog_loop")
    _loop_form = _loop_rng.randrange(3)
    if _loop_form == 1:
        _loop_head = "    while 1 == 1:\n"
    elif _loop_form == 2:
        _loop_head = "    while not (1 != 1):\n"
    else:
        _loop_head = "    while True:\n"
    _check = (
        f"getattr({_w_s}, {_h_gt})() is not None or "
        f"getattr({_w_s}, {_h_gp}, lambda: None)() or "
        f"({_w_m} and any({_w_m}.get_tool(i) for i in (1, 2, 3))) or "
        f"any(type(getattr(__import__({_h_bi}), n, None)).__name__ != "
        f"'builtin_function_or_method' for n in "
        f"({_h_x1}, {_h_x2}, {_h_x3}, {_h_x4})) or "
        f"'pdb' in getattr({_w_s}, {_h_md}) or "
        f"'bdb' in getattr({_w_s}, {_h_md}) or "
        f"'pydevd' in getattr({_w_s}, {_h_md}) or "
        f"'trace' in getattr({_w_s}, {_h_md})"
    )
    _kill = f"getattr(__import__({_h_os}), {_h_ex})(1)"
    def_src = (
        f"def {wd_func}():\n"
        f"    {_w_s} = __import__({_h_sy})\n"
        f"    {_w_t} = __import__({_h_tm})\n"
        f"    {_w_m} = getattr({_w_s}, {_h_mo}, None)\n"
        + _loop_head +
        f"        {_w_t0} = getattr({_w_t}, {_h_pc})()\n"
        f"        getattr({_w_t}, {_h_sl})({iv})\n"
        f"        if getattr({_w_t}, {_h_pc})() - {_w_t0} > {iv * 10 + 5} or {_check}: {_kill}\n"
    )
    start_src = f"getattr({_w_thr}(target={wd_func}, daemon=True), {_h_st})()\n"
    return def_src, start_src
def _fragment_payload(payload_b85: bytes, build_seed: int,
                      fragment_count: int = 6) -> List[bytes]:
    n = max(2, fragment_count)
    total = len(payload_b85)
    base = total // n
    fragments: List[bytes] = []
    pos = 0
    rng = _seeded_rng(build_seed, "anti_dump_fragment_sizes")
    for i in range(n - 1):
        jitter = rng.randint(-max(1, base // 8), max(1, base // 8))
        size = max(1, base + jitter)
        size = min(size, total - pos - (n - 1 - i))
        if size <= 0:
            size = 1
        fragments.append(payload_b85[pos:pos + size])
        pos += size
    fragments.append(payload_b85[pos:])
    return fragments
def _derive_keystream(build_seed: int, bucket_idx: int, length: int,
                      mix: str = "", tag: str = "anti_decompli") -> bytes:
    stream = bytearray()
    block_idx = 0
    while len(stream) < length:
        block = hashlib.sha256(
            f"{build_seed}{mix}::{tag}::{bucket_idx}::{block_idx}".encode()
        ).digest()
        stream.extend(block)
        block_idx += 1
    return bytes(stream[:length])
def _xor_bytes_fast(data: bytes, key: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    data_int = int.from_bytes(data, "big")
    key_int = int.from_bytes(key, "big")
    return (data_int ^ key_int).to_bytes(n, "big")
def _anti_decompli_encode(payload_b85: bytes, build_seed: int,
                          mix: str = "") -> Dict[str, Any]:
    poly = _envelope_poly(build_seed)
    dc_tag = poly["dc_tag"]
    tok_hex = poly["token_hex"]
    tok_int = poly["token_int"]
    mid = poly["mask_id"]
    order = poly["order"]
    plen = len(payload_b85)
    _sdrng = _seeded_rng(build_seed, "ad_seed_split")
    _s1 = _sdrng.getrandbits(max(31, build_seed.bit_length()))
    _s2 = _sdrng.getrandbits(max(31, build_seed.bit_length()))
    _s3 = _s1 ^ _s2 ^ build_seed
    seed_expr, seed_num = _poly_seed_expr(poly["seed_shape"], _s1, _s2, _s3)
    dc_expr = _poly_dc_expr(poly["dc_parts"])
    base = {"order": order, "seed_expr": seed_expr, "dc_expr": dc_expr,
            "mask_id": mid, "plen": plen, "poly": poly}
    if order == "SEQ":
        ks_cont = _derive_keystream(seed_num, 0, plen, mix, dc_tag)
        t2 = int(hashlib.sha256(ks_cont).hexdigest()[:2], 16) ^ tok_int
        m0 = bytes(_poly_mask_enc(mid, b, i, plen, t2)
                   for i, b in enumerate(payload_b85))
        enc = _xor_bytes_fast(m0, ks_cont)
        base.update({"n": 1, "blob": enc, "buckets": None})
        return base
    n = poly["n_buckets"]
    lens = [(plen - b + n - 1) // n for b in range(n)]
    kss = [_derive_keystream(seed_num, b, lens[b], mix, dc_tag)
           for b in range(n)]
    if order == "IN":
        enc: Dict[int, bytes] = {}
        for b in range(n):
            t2b = int(hashlib.sha256(kss[b]).hexdigest()[:2], 16) ^ tok_int
            plain_b = payload_b85[b::n]
            masked_b = bytes(_poly_mask_enc(mid, x, i, lens[b], t2b)
                             for i, x in enumerate(plain_b))
            enc[b] = _xor_bytes_fast(masked_b, kss[b])
        base.update({"n": n, "blob": None, "buckets": enc})
        return base
    ks_mat = b"".join(kss)
    t2 = int(hashlib.sha256(ks_mat).hexdigest()[:2], 16) ^ tok_int
    masked = bytes(_poly_mask_enc(mid, b, i, plen, t2)
                   for i, b in enumerate(payload_b85))
    out: Dict[int, bytes] = {}
    for b in range(n):
        out[b] = _xor_bytes_fast(masked[b::n], kss[b])
    base.update({"n": n, "blob": None, "buckets": out})
    return base
def _anti_decompli_decode_source(func_name: str, spec: Dict[str, Any],
                                 build_seed: int) -> str:
    order = spec["order"]
    n = spec["n"]
    mid = spec["mask_id"]
    seed_expr = spec["seed_expr"]
    dc_expr = spec["dc_expr"]
    _h_hl2 = _hidden_name_src("hashlib", build_seed, "hidmod")
    _h_s256_2 = _hidden_name_src("sha256", build_seed, "hidattr")
    _h_hex_2 = _hidden_name_src("hexdigest", build_seed, "hidattr")
    _ad: Dict[str, str] = {}
    for _ck in ("hl", "bk", "dc", "num", "ks", "bi", "ln", "blk",
                "xo", "lf", "rt", "dl", "out", "elt", "tk", "ub",
                "kb", "t2", "hx", "ei", "eb", "tp"):
        _pp = f"ad::v::{_ck}"
        _nm = _hira_token(build_seed, _pp, 3)
        while _nm in _ad.values():
            _pp += "x"
            _nm = _hira_token(build_seed, _pp, 3)
        _ad[_ck] = _nm
    _V = _ad.__getitem__
    _ks_line = (
        f"    {_V('ks')} = lambda {_V('bi')}, {_V('ln')}: b''.join(getattr({_V('hl')}, {_h_s256_2})((str({seed_expr}) + {_V('tk')}"
        f" + {dc_expr} + str({_V('bi')}) + '::' + str({_V('blk')})).encode()).digest() "
        f"for {_V('blk')} in range(({_V('ln')} >> 5) + 1))[:{_V('ln')}]\n")
    _xo_line = (
        f"    {_V('xo')} = lambda {_V('lf')}, {_V('rt')}: (int.from_bytes({_V('lf')}, 'big') ^ "
        f"int.from_bytes({_V('rt')}, 'big')).to_bytes(len({_V('lf')}), 'big') if {_V('lf')} else b''\n")
    _head = (f"def {func_name}({_V('tk')}):\n"
             f"    {_V('hl')} = __import__({_h_hl2})\n")
    _t2_from = (f"(int(getattr(getattr({_V('hl')}, {_h_s256_2})({{X}}), {_h_hex_2})()[:2], 16)"
                f" ^ int({_V('tk')}[:2] or '0', 16))")
    if order == "SEQ":
        blob = spec["blob"]
        _chunks = [repr(blob[i:i + 32768]) for i in range(0, len(blob), 32768)] or ["b''"]
        _bk_line = f"    {_V('bk')} = (\n" + "\n".join("        " + c for c in _chunks) + ")\n"
        _body = (
            f"    {_V('kb')} = {_V('ks')}(0, len({_V('bk')}))\n"
            f"    return {_V('xo')}({_V('bk')}, {_V('kb')}), {_V('kb')}\n")
        return _head + _bk_line + _ks_line + _xo_line + _body
    buckets = spec["buckets"]
    entries = [f"{k!r}: {v!r}" for k, v in sorted(buckets.items())]
    entry_lines = [
        "    " + ", ".join(entries[i:i + 3]) + ","
        for i in range(0, len(entries), 3)]
    entry_lines[-1] = entry_lines[-1].rstrip(",")
    _bk_line = f"    {_V('bk')} = {{\n" + "\n".join(entry_lines) + "}\n"
    _num_line = f"    {_V('dc')} = {n}; {_V('num')} = {n}\n"
    if order == "IN":
        _eb, _ei, _tp = _V('eb'), _V('ei'), _V('tp')
        _unmask_elt = _poly_mask_dec_src(
            mid, _eb, _ei, f"len({_V('bk')}[{_V('bi')}])", _V('t2'))
        _body = (
            f"    {_V('ub')} = {{}}\n"
            f"    for {_V('bi')} in range({_V('num')}):\n"
            f"        {_V('kb')} = {_V('ks')}({_V('bi')}, len({_V('bk')}[{_V('bi')}]))\n"
            f"        {_V('t2')} = {_t2_from.format(X=_V('kb'))}\n"
            f"        {_tp} = {_V('xo')}({_V('bk')}[{_V('bi')}], {_V('kb')})\n"
            f"        {_V('ub')}[{_V('bi')}] = bytes({_unmask_elt} for {_ei}, {_eb} in enumerate({_tp}))\n"
            f"    {_V('out')} = bytearray(sum(len({_V('elt')}) for {_V('elt')} in {_V('ub')}.values()))\n"
            f"    for {_V('bi')} in range({_V('num')}): {_V('out')}[{_V('bi')}::{_V('num')}] = {_V('ub')}[{_V('bi')}]\n"
            f"    return bytes({_V('out')})\n")
        return _head + _bk_line + _num_line + _ks_line + _xo_line + _body
    _body = (
        f"    {_V('dl')} = [{_V('xo')}({_V('bk')}[{_V('bi')}], {_V('ks')}({_V('bi')}, len({_V('bk')}[{_V('bi')}]))) "
        f"for {_V('bi')} in range({_V('num')})]\n"
        f"    {_V('out')} = bytearray(sum(len({_V('elt')}) for {_V('elt')} in {_V('dl')}))\n"
        f"    for {_V('bi')} in range({_V('num')}): {_V('out')}[{_V('bi')}::{_V('num')}] = {_V('dl')}[{_V('bi')}]\n"
        f"    return bytes({_V('out')})\n")
    return _head + _bk_line + _num_line + _ks_line + _xo_line + _body
def _decompiler_noise_source(build_seed: int, naming: "RuneNaming",
                             rounds: int = 24) -> str:
    fn = naming.generate(f"noise::id::{build_seed}")
    acc = naming.generate(f"noise::acc::{build_seed}")
    exprs = []
    for i in range(rounds):
        depth = 3 + (i % 4)
        exprs.append(f"{acc}.append(" + f"{fn}(" * depth + "''"
                     + ")" * depth + ")")
    lines = [f"def {fn}(_):", "    return _", f"{acc} = []",
             "try: " + ";".join(exprs), "except Exception: pass"]
    _nrng = _seeded_rng(build_seed, "decompiler_noise")
    _traps = [("(1 // 0,)", "ZeroDivisionError"),
              ("(1 % 0,)", "ZeroDivisionError"),
              ("(1.0 // 0.0,)", "ZeroDivisionError"),
              ("([][9],)", "IndexError"),
              ("([][0],)", "IndexError"),
              ("({}[9],)", "KeyError"),
              ("({}['k'],)", "KeyError"),
              ("(int('x'),)", "ValueError"),
              ("(float('y'),)", "ValueError"),
              ("(chr(1114112),)", "ValueError"),
              ("([1].index(2),)", "ValueError"),
              ("(next(iter([])),)", "StopIteration")]
    for _texpr, _texc in _nrng.sample(_traps, 2):
        lines.append(f"try: {_texpr}")
        lines.append(f"except {_texc}: pass")
    return "\n".join(lines) + "\n"
def _stream_split_source(body_source: str, build_seed: int,
                          naming_hira: "RuneNaming",
                          compression_level: int = 9,
                          scrub: Optional[List[str]] = None,
                          ) -> Tuple[str, int]:
    import types as _types
    tree = ast.parse(body_source)
    if _is_entry_wrapped_tree(tree) is not None:
        container = tree.body[0].body
    else:
        container = tree.body
    candidates: List[Tuple[ast.FunctionDef, str]] = []
    def _eligible(fn: ast.FunctionDef) -> bool:
        for n in ast.walk(fn):
            if isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)):
                return False
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id == "super" and not n.args and not n.keywords:
                return False
        try:
            probe = compile(ast.Module(body=[copy.deepcopy(fn)],
                                       type_ignores=[]),
                            "<dzydkh>", "exec")
        except Exception:
            return False
        for const in probe.co_consts:
            if isinstance(const, _types.CodeType) and const.co_name == fn.name:
                return not const.co_freevars
        return False
    def _collect(stmts: List[ast.stmt], prefix: str) -> None:
        for st in list(stmts):
            if isinstance(st, ast.AsyncFunctionDef):
                _collect(list(st.body), prefix + (getattr(st, "name", "?") + "."))
            elif isinstance(st, ast.FunctionDef):
                if _eligible(st):
                    candidates.append((st, prefix + st.name))
                else:
                    _collect(list(st.body), prefix + st.name + ".")
            elif isinstance(st, ast.ClassDef):
                for sub in list(st.body):
                    if isinstance(sub, ast.AsyncFunctionDef):
                        continue
                    if isinstance(sub, ast.FunctionDef):
                        if _eligible(sub):
                            candidates.append((sub, prefix + st.name + "." + sub.name))
    _collect(container, "")
    if not candidates:
        return body_source, 0
    _poly_s = _envelope_poly(build_seed)
    tok_hex = _poly_s["token_hex"]
    tok_int = _poly_s["token_int"]
    _smask = _poly_s["mask_id"]
    _spoly_pre = _poly_s["guard_preimage"].encode()
    disp_name = naming_hira.generate(f"stream::disp::{build_seed}")
    vault_name = naming_hira.generate(f"stream::vault::{build_seed}")
    vaults: Dict[str, bytes] = {}
    def _fn_key(_key: str) -> int:
        return (int(hashlib.sha256(
            (tok_hex + _key).encode()).hexdigest()[:2], 16) ^ tok_int) & 255
    for idx, (fn, qual) in enumerate(candidates):
        key = f"F{idx}"
        probe = compile(ast.Module(body=[copy.deepcopy(fn)],
                                   type_ignores=[]),
                        "<dzydkh>", "exec")
        fco = next(c for c in probe.co_consts
                   if isinstance(c, _types.CodeType) and c.co_name == fn.name)
        raw = marshal.dumps(fco)
        try:
            _sseed = int(hashlib.sha256(
                f"{build_seed}::pycdc::{key}".encode()).hexdigest()[:16], 16)
            fco = _pycdc_harden_code(fco, _sseed, max_codes=4, depth=8,
                                     breadth=1)
            raw = marshal.dumps(fco)
        except Exception:
            pass
        comp = _compress_for_poly(raw, _poly_s, compression_level)
        enc = base64.b85encode(comp)
        masked = bytes(_poly_mask_enc(_smask, b, i, len(enc), _fn_key(key))
                       for i, b in enumerate(enc))
        vaults[key] = masked
        pargs = [a.arg for a in list(fn.args.posonlyargs) + list(fn.args.args)]
        kwnames = [a.arg for a in fn.args.kwonlyargs]
        pargs_expr = ast.Tuple(
            elts=[ast.Name(id=a, ctx=ast.Load()) for a in pargs]
            + ([ast.Starred(value=ast.Name(id=fn.args.vararg.arg, ctx=ast.Load()),
                             ctx=ast.Load())] if fn.args.vararg else []),
            ctx=ast.Load())
        kws_expr = ast.Dict(
            keys=[ast.Constant(value=k) for k in kwnames]
            + ([None] if fn.args.kwarg else []),
            values=[ast.Name(id=k, ctx=ast.Load()) for k in kwnames]
            + ([ast.Name(id=fn.args.kwarg.arg, ctx=ast.Load())]
               if fn.args.kwarg else []))
        fn.body = [ast.Return(value=ast.Call(
            func=ast.Name(id=disp_name, ctx=ast.Load()),
            args=[ast.Constant(value=key), pargs_expr, kws_expr],
            keywords=[]))]
        ast.fix_missing_locations(fn)
    disp_src = (
        f"def {disp_name}(_k, _pa, _kw):\n"
        f"    _raw = {vault_name}[_k]\n"
        f"    _n = len(_raw)\n"
        f"    _tok = __import__('hashlib').sha256({_spoly_pre!r}).hexdigest()[:16]\n"
        f"    _kt = (int(__import__('hashlib').sha256((_tok + _k).encode()).hexdigest()[:2], 16) ^ int(_tok[:2], 16)) & 255\n"
        f"    _data = bytes({_poly_mask_dec_src(_smask, '_b', '_i', '_n', '_kt')} for _i, _b in enumerate(_raw))\n"
        f"    _m = __import__('marshal'); _z = __import__('{_poly_s.get('comp', 'zlib')}'); _b = __import__('base64'); _t = __import__('types')\n"
        f"    _co = _m.loads(_z.decompress(_b.b85decode(_data)))\n"
        f"    return _t.FunctionType(_co, globals())(*_pa, **_kw)\n"
    )
    vault_src = f"{vault_name} = {vaults!r}\n"
    disp_node = ast.parse(disp_src).body[0]
    vault_node = ast.parse(vault_src).body[0]
    mark_generated(disp_node)
    mark_generated(vault_node)
    tree.body[_module_insert_index(tree):_module_insert_index(tree)] = (
        [vault_node, disp_node])
    ast.fix_missing_locations(tree)
    residual = ast.unparse(tree)
    ast.parse(residual)
    if scrub is not None:
        scrub.append(disp_name)
        scrub.append(vault_name)
    return residual, len(candidates)
def _pycdc_harden_code(co, seed: int, max_codes: int = 24,
                       depth: int = 12, breadth: int = 2):
    import types as _t
    rng = _seeded_rng(int(seed) & 0xFFFFFFFFFFFFFFFF, "pycdc_hostile")
    budget = [int(max_codes)]
    def _junk_chain():
        inner = compile("0", "<dzydkh>", "eval")
        for _ in range(int(depth)):
            try:
                inner = inner.replace(co_consts=(inner, 0))
            except Exception:
                break
        return inner
    def _rec(c):
        consts = []
        changed = False
        for k in c.co_consts:
            if isinstance(k, _t.CodeType):
                k2 = _rec(k)
                consts.append(k2)
                changed = changed or (k2 is not k)
            else:
                consts.append(k)
        if budget[0] > 0:
            budget[0] -= 1
            for _ in range(int(breadth)):
                try:
                    consts.append(_junk_chain())
                except Exception:
                    break
            changed = True
        if not changed:
            return c
        try:
            return c.replace(co_consts=tuple(consts))
        except Exception:
            return c
    try:
        return _rec(co)
    except Exception:
        return co
def _build_payload_bytes(obfuscated_source: str,
                         source_filename: str = "<dzydkh>",
                         compression_level: int = 9,
                         comp: str = "zlib") -> bytes:
    source_filename = "<dzydkh>"
    code_obj = compile(obfuscated_source, source_filename, "exec")
    _pseed = int(hashlib.sha256(obfuscated_source.encode("utf-8")).hexdigest()[:16], 16)
    code_obj = _pycdc_harden_code(code_obj, _pseed)
    raw = marshal.dumps(code_obj)
    compressed = _compress_for_poly(
        raw, {"comp": comp}, compression_level)
    encoded = base64.b85encode(compressed)
    return encoded
def _zx_globals_key_src(key: str, build_seed: int) -> str:
    data = key.encode("utf-8")
    elts = []
    shape_rng = _seeded_rng(build_seed, f"zxkey::shape::{key}")
    for i, b in enumerate(data):
        kd = hashlib.sha256(
            f"{build_seed}::zxkey::{key}::{i}".encode()).digest()
        k1, k2 = kd[0], kd[1] | 1
        _a = _cjk_lambda_arg(build_seed, "zxkey", i)
        elts.append(_hidden_char_expr(_a, k1, k2, b, shape_rng))
    _zwrap = _seeded_rng(build_seed, f"zxkey::wrap::{key}").randrange(3)
    if _zwrap == 1:
        return "bytes(bytearray([" + ", ".join(elts) + '])).decode("utf-8")'
    if _zwrap == 2:
        return "bytearray([" + ", ".join(elts) + ']).decode("utf-8")'
    return "bytes([" + ", ".join(elts) + ']).decode("utf-8")'
@functools.lru_cache(maxsize=4096)
def _hidden_name_src(name: str, build_seed: int, purpose: str) -> str:
    elts = []
    shape_rng = _seeded_rng(build_seed, f"hidden::shape::{purpose}::{name}")
    for i, ch in enumerate(name):
        kd = hashlib.sha256(
            f"{build_seed}::{purpose}::{name}::{i}".encode()).digest()
        k1, k2 = kd[0], kd[1] | 1
        _a = _cjk_lambda_arg(build_seed, "hidname", i)
        elts.append(_hidden_char_expr(_a, k1, k2, ord(ch), shape_rng))
    _wrap_rng = _seeded_rng(build_seed, f"hidden::wrap::{purpose}::{name}")
    _wpick = _wrap_rng.randrange(4)
    if _wpick == 1:
        return "\"\".join([" + ",".join(
            "chr(" + e + ")" for e in elts) + "])"
    if _wpick == 2:
        return "str().join(map(chr,[" + ",".join(elts) + "]))"
    if _wpick == 3:
        return "''.join(chr(_e)for _e in[" + ",".join(elts) + "])"
    return "''.join(map(chr,[" + ",".join(elts) + "]))"
def _make_output_architecture(payload_b85: bytes,
                               username: str,
                               naming_cjk: RuneNaming,
                               naming_hira: RuneNaming,
                               build_seed: int,
                               enable_guard: bool,
                               target_version: Tuple[int, int],
                               config: Optional["DragonConfig"] = None,
                               scrub_names: Optional[List[str]] = None) -> Tuple[str, Dict[str, bool], bool]:
    cfg = config
    anti_combo_status = {"anti_debug": False,
                         "anti_hook": False, "anti_dump": False}
    anti_decompli_status = False
    _plen = len(payload_b85)
    _poly = _envelope_poly(build_seed)
    _tok_hex = _poly["token_hex"]
    _tok_int = _poly["token_int"]
    try:
        _compressed_for_hash = base64.b85decode(payload_b85)
    except Exception:
        _compressed_for_hash = b""
    _phash = hashlib.sha256(_compressed_for_hash).hexdigest()
    _h_marshal = _hidden_name_src("marshal", build_seed, "hidmod")
    _h_zlib = _hidden_name_src(_poly.get("comp", "zlib"), build_seed, "hidmod")
    _h_hashlib = _hidden_name_src("hashlib", build_seed, "hidmod")
    _h_base64 = _hidden_name_src("base64", build_seed, "hidmod")
    _h_builtins = _hidden_name_src("builtins", build_seed, "hidmod")
    _h_exec = _hidden_name_src("exec", build_seed, "hidattr")
    _h_loads = _hidden_name_src("loads", build_seed, "hidattr")
    _h_decompress = _hidden_name_src("decompress", build_seed, "hidattr")
    _h_sha256 = _hidden_name_src("sha256", build_seed, "hidattr")
    _h_hexdigest = _hidden_name_src("hexdigest", build_seed, "hidattr")
    _h_b85decode = _hidden_name_src("b85decode", build_seed, "hidattr")
    _b85call = f"getattr(__import__({_h_base64}), {_h_b85decode})"
    _h_sysexit = _hidden_name_src("SystemExit", build_seed, "hidattr")
    _h_os2 = _hidden_name_src("os", build_seed, "hidmod:run")
    _h_open = _hidden_name_src("open", build_seed, "hidattr:run")
    _h_system = _hidden_name_src("system", build_seed, "hidattr:run")
    _raise_se = f"raise getattr(__import__({_h_builtins}), {_h_sysexit})(1)"
    h_self = naming_hira.generate(f"user::self::{build_seed}")
    h_raw = naming_hira.generate(f"user::var::raw::{build_seed}")
    h_ii = naming_hira.generate(f"user::var::idx::{build_seed}")
    h_cc = naming_hira.generate(f"user::var::chr::{build_seed}")
    _arch_rng = _seeded_rng(build_seed, "arch_class_templates")
    payload_sha = hashlib.sha256(
        f"payload_class::{build_seed}".encode()
    ).hexdigest()[:6]
    _pay_prefix, _pay_sep = _arch_rng.choice(
        [("龍焔", "_"), ("龍炎", "_"), ("龍焔", ""), ("黒龍", "_"),
         ("玄龍", "_"), ("蒼炎", ""), ("冥凰", "_"), ("焱虎", "_"),
         ("影雀", "_"), ("霧麟", ""), ("幻鳳", "_"), ("雷牙", "_")])
    payload_class_name = f"{_pay_prefix}{_pay_sep}{payload_sha}"
    p_attr_data   = naming_cjk.generate(f"payload::data::{build_seed}")
    p_meth_get    = naming_cjk.generate(f"payload::get::{build_seed}")
    _safe_user = "".join(
        ch if (ch.isalnum() or ch == "_") else "_" for ch in str(username)
    ) or "Username"
    if _safe_user[0].isdigit():
        _safe_user = "_" + _safe_user
    _user_conn = _arch_rng.choice(["ᅠ", "__", "_"])
    user_class_name = f"User{_user_conn}{_safe_user}"
    h_param_pl    = naming_hira.generate(f"user::param::pl::{build_seed}")
    h_attr_data   = naming_hira.generate(f"user::attr::data::{build_seed}")
    h_meth_run    = naming_hira.generate(f"user::meth::run::{build_seed}")
    h_import_m    = naming_hira.generate(f"user::import::marshal::{build_seed}")
    h_import_z    = naming_hira.generate(f"user::import::zlib::{build_seed}")
    h_import_h    = naming_hira.generate(f"user::import::hashlib::{build_seed}")
    h_var_code = naming_hira.generate(f"user::var::code::{build_seed}")
    h_dck = naming_hira.generate(f"user::var::dck::{build_seed}")
    _canary_plain, _canary_blob = _canary_pair(
        build_seed, _poly.get("comp", "zlib"))
    h_var_exec = naming_hira.generate(f"user::var::exec::{build_seed}")
    h_tok = naming_hira.generate(f"user::param::tok::{build_seed}")
    h_ksm = naming_hira.generate(f"user::var::ksm::{build_seed}")
    h_t2 = naming_hira.generate(f"user::var::t2::{build_seed}")
    h_kbi = naming_hira.generate(f"user::var::kbi::{build_seed}")
    h_kblk = naming_hira.generate(f"user::var::kblk::{build_seed}")
    h_kln = naming_hira.generate(f"user::var::kln::{build_seed}")
    h_m1 = naming_hira.generate(f"user::var::m1::{build_seed}")
    h_pl = naming_hira.generate(f"boot::pl::{build_seed}")
    h_btok = naming_hira.generate(f"boot::tok::{build_seed}")
    b_inst        = naming_hira.generate(f"boot::inst::{build_seed}")
    b_exc         = naming_hira.generate(f"boot::exc::{build_seed}")
    anti_combo_funcs_src = ""
    anti_fn_call = ""
    anti_preexec_call = ""
    watchdog_src = ""
    wd_start_line = ""
    use_anti_combo = bool(cfg and cfg.anti_combo)
    if enable_guard:
        anti_fn_trace = naming_hira.generate(f"anti_analysis::trace::{build_seed}")
        anti_fn_hook = naming_hira.generate(f"anti_analysis::hook::{build_seed}")
        anti_combo_funcs_src += _anti_analysis_source(anti_fn_trace, build_seed, half="trace") + "\n"
        anti_combo_funcs_src += _anti_analysis_source(anti_fn_hook, build_seed, half="hook") + "\n"
        anti_fn_extra = naming_hira.generate(f"anti_analysis::extra::{build_seed}")
        anti_combo_funcs_src += _extra_guard_source(anti_fn_extra, build_seed) + "\n"
        wd_func = naming_hira.generate(f"watchdog::{build_seed}")
        _wd_def, _wd_start = _watchdog_source(wd_func, build_seed)
        watchdog_src = _wd_def + "\n"
        wd_start_line = f"    {_wd_start.strip()}\n"
        anti_fn_call = f"        {anti_fn_trace}()\n        {anti_fn_hook}()\n        {anti_fn_extra}()\n"
        anti_preexec_call = f"        {anti_fn_hook}()\n        {anti_fn_extra}()\n"
        anti_combo_status["anti_debug"] = True
        if use_anti_combo and cfg.anti_hook:
            anti_combo_status["anti_hook"] = True
    use_anti_decompli = bool(cfg and cfg.enable_anti_decompli)
    anti_decompli_func_src = ""
    ad_func_name = None
    cnt_key_src = _zx_globals_key_src(h_attr_data + "cnt", build_seed)
    if use_anti_decompli:
        ad_func_name = naming_hira.generate(f"anti_decompli::func::{build_seed}")
        ad_spec = _anti_decompli_encode(payload_b85, build_seed,
                                        mix=_tok_hex)
        anti_decompli_func_src = _anti_decompli_decode_source(
            ad_func_name, ad_spec, build_seed
        ) + "\n"
        anti_decompli_status = True
    use_anti_dump = bool(use_anti_combo and cfg.anti_dump and not use_anti_decompli)
    if use_anti_dump:
        _masked_whole = bytes(
            _poly_mask_enc(_poly["mask_id"], b, i, _plen, _tok_int)
            for i, b in enumerate(payload_b85))
        fragments = _fragment_payload(
            _masked_whole, build_seed,
            fragment_count=(9 if (cfg and cfg.more_obfuscation) else 6))
        frag_attr_names = [
            naming_cjk.generate(f"payload::frag::{i}::{build_seed}")
            for i in range(len(fragments))
        ]
        anti_combo_status["anti_dump"] = True
    elif not use_anti_decompli:
        _n1 = _plen // 2
        _plain_a, _plain_b = payload_b85[:_n1], payload_b85[_n1:]
        _t2_build = (int(hashlib.sha256(_plain_a).hexdigest()[:2], 16)
                     ^ _tok_int)
        _masked_a = bytes(
            _poly_mask_enc(_poly["mask_id"], b, i, _plen, _tok_int)
            for i, b in enumerate(_plain_a))
        _masked_b = bytes(
            _poly_mask_enc(_poly["mask_id"], b, i + _n1, _plen, _t2_build)
            for i, b in enumerate(_plain_b))
        p_attr_data2 = naming_cjk.generate(f"payload::data2::{build_seed}")
        fragments = [_masked_a, _masked_b]
        frag_attr_names = [p_attr_data, p_attr_data2]
    else:
        fragments = []
        frag_attr_names = []
    cnt_key_src = _zx_globals_key_src(h_attr_data + "cnt", build_seed)
    _tokint_expr = f"int({h_tok}[:2] or '0', 16)"
    if use_anti_decompli:
        frag_attr_lines = ""
        _ad_order = ad_spec["order"]
        _ad_mid = ad_spec["mask_id"]
        if _ad_order == "IN":
            get_body = (f"        globals()[{cnt_key_src}] = globals().get({cnt_key_src}, 0) + 1\n"
                        f"        {h_raw} = {ad_func_name}({h_tok})\n"
                        f"        if len({h_raw}) != {_plen}: {_raise_se}\n"
                        f"        return {_b85call}({h_raw})\n")
        elif _ad_order == "SEQ":
            _t2_seq = (f"        {h_t2} = (int(getattr(getattr(__import__({_h_hashlib}), {_h_sha256})({h_ksm}), {_h_hexdigest})()[:2], 16)"
                       f" ^ {_tokint_expr})\n")
            _ret_seq = (f"        return {_b85call}(bytes({_poly_mask_dec_src(_ad_mid, h_cc, h_ii, str(_plen), h_t2)}"
                        f" for {h_ii},{h_cc} in enumerate({h_raw})))\n")
            get_body = (f"        globals()[{cnt_key_src}] = globals().get({cnt_key_src}, 0) + 1\n"
                        f"        {h_raw}, {h_ksm} = {ad_func_name}({h_tok})\n"
                        f"        if len({h_raw}) != {_plen}: {_raise_se}\n"
                        + _t2_seq + _ret_seq)
        else:
            _n = ad_spec["n"]
            _lens_tup = "(" + ", ".join(
                str((_plen - b + _n - 1) // _n) for b in range(_n)) + ")"
            _ksm_expr = (f"b\"\".join(b\"\".join(getattr(__import__({_h_hashlib}), {_h_sha256})((str({ad_spec['seed_expr']}) + {h_tok}"
                         f" + {ad_spec['dc_expr']} + str({h_kbi}) + '::' + str({h_kblk})).encode()).digest()"
                         f" for {h_kblk} in range(({h_kln} >> 5) + 1))[:{h_kln}]"
                         f" for {h_kbi}, {h_kln} in zip(range({_n}), {_lens_tup}))")
            _t2_out = (f"        {h_t2} = (int(getattr(getattr(__import__({_h_hashlib}), {_h_sha256})({h_ksm}), {_h_hexdigest})()[:2], 16)"
                       f" ^ {_tokint_expr})\n")
            _ret_out = (f"        return {_b85call}(bytes({_poly_mask_dec_src(_ad_mid, h_cc, h_ii, str(_plen), h_t2)}"
                        f" for {h_ii},{h_cc} in enumerate({h_raw})))\n")
            get_body = (f"        globals()[{cnt_key_src}] = globals().get({cnt_key_src}, 0) + 1\n"
                        f"        {h_raw} = {ad_func_name}({h_tok})\n"
                        f"        if len({h_raw}) != {_plen}: {_raise_se}\n"
                        f"        {h_ksm} = {_ksm_expr}\n"
                        + _t2_out + _ret_out)
    else:
        frag_attr_lines = "".join(
            f"    {name} = {repr(frag)}\n" for name, frag in zip(frag_attr_names, fragments)
        )
        if use_anti_dump:
            reassemble_expr = " + ".join(f"{h_self}.{name}" for name in frag_attr_names)
            get_body = (f"        globals()[{cnt_key_src}] = globals().get({cnt_key_src}, 0) + 1\n"
                        f"        {h_raw} = {reassemble_expr}\n"
                        f"        if len({h_raw}) != {_plen}: {_raise_se}\n"
f"        return {_b85call}(bytes({_poly_mask_dec_src(_poly['mask_id'], h_cc, h_ii, str(_plen), _tokint_expr)} "
                    f"for {h_ii},{h_cc} in enumerate({h_raw})))\n")
        else:
            _n1 = _plen // 2
            _unmask_a = _poly_mask_dec_src(
                _poly['mask_id'], h_cc, h_ii, str(_plen), _tokint_expr)
            _unmask_b = _poly_mask_dec_src(
                _poly['mask_id'],
                h_cc, f"({h_ii} + {_n1})", str(_plen), h_t2)
            get_body = (f"        globals()[{cnt_key_src}] = globals().get({cnt_key_src}, 0) + 1\n"
                        f"        if len({h_self}.{p_attr_data}) + len({h_self}.{frag_attr_names[1]}) != {_plen}: {_raise_se}\n"
                        f"        {h_m1} = bytes({_unmask_a} for {h_ii},{h_cc} in enumerate({h_self}.{p_attr_data}))\n"
                        f"        {h_t2} = (int(getattr(getattr(__import__({_h_hashlib}), {_h_sha256})({h_m1}), {_h_hexdigest})()[:2], 16) ^ {_tokint_expr})\n"
                        f"        {h_raw} = {h_m1} + bytes({_unmask_b} for {h_ii},{h_cc} in enumerate({h_self}.{frag_attr_names[1]}))\n"
                        f"        del {h_m1}\n"
                        f"        if len({h_raw}) != {_plen}: {_raise_se}\n"
                        f"        return {_b85call}({h_raw})\n")
    payload_class_src = (
        f"class {payload_class_name}:\n"
        + frag_attr_lines
        + f"    def {p_meth_get}({h_self}, {h_tok}):\n"
        + get_body
    )
    zx_key_src = _zx_globals_key_src(h_attr_data, build_seed)
    _w_rng = _seeded_rng(build_seed, "wraith::decoys")
    _decoy_lines = ""
    _n_decoy = 2 + _w_rng.randint(0, 2)
    for _di in range(_n_decoy):
        _dvar = naming_hira.generate(f"wraith::decoy::{_di}::{build_seed}")
        if _di == 0:
            _pad = max(256, _plen - 512)
            _dead_src = ('"""\n' + "".join(
                chr(_w_rng.randrange(97, 123)) for _ in range(_pad)
            ) + '\n"""\n' + _dvar + " = 0\n")
            try:
                _dead_co = compile(_dead_src, "<dzydkh>", "exec")
                _garbage = _compress_for_poly(marshal.dumps(_dead_co), _poly, 9)
            except Exception:
                _garbage = _compress_for_poly(bytes(_w_rng.randrange(256)
                                               for _ in range(max(64, _plen // 4))), _poly, 9)
        else:
            _garbage = _compress_for_poly(bytes(_w_rng.randrange(256)
                                           for _ in range(max(64, _plen // 4))), _poly, 9)
        _decoy_lines += (
            f"        try: {_dvar} = getattr(globals()[{zx_key_src}][1], {_h_loads})(getattr(globals()[{zx_key_src}][2], {_h_decompress})({_garbage!r}))\n"
            f"        except Exception: {_dvar} = None\n")
    _real_loads = (
        f"        try: {h_var_code} = getattr(globals()[{zx_key_src}][1], {_h_loads})(getattr(globals()[{zx_key_src}][2], {_h_decompress})(globals()[{zx_key_src}][3]))\n"
        f"        except Exception: {h_var_code} = None\n"
        f"        if {h_var_code} is None: {_raise_se}\n")
    user_class_src = (
        f"class {user_class_name}:\n"
        f"    def {h_meth_run}({h_self}, {h_param_pl}):\n"
        + anti_fn_call
        + f"        {h_import_m} = __import__({_h_marshal});{h_import_z} = __import__({_h_zlib});{h_import_h} = __import__({_h_hashlib})\n"
        f"        {h_var_exec} = getattr(__import__({_h_builtins}), {_h_exec})\n"
        f"        globals()[{zx_key_src}] = ({h_self}, {h_import_m},"
        f" {h_import_z}, {h_param_pl})\n"
        f"        del {h_import_m}, {h_import_z}\n"
        f"        if globals().get({cnt_key_src}, 0) != 1: {_raise_se}\n"
        f"        if getattr(getattr({h_import_h}, {_h_sha256})(globals()[{zx_key_src}][3]), {_h_hexdigest})() != {_phash!r}: {_raise_se}\n"
        + anti_preexec_call
        + f"        if type(getattr(__import__({_h_builtins}), {_h_open})).__name__ != 'builtin_function_or_method' or type(getattr(__import__({_h_os2}), {_h_system}, None)).__name__ != 'builtin_function_or_method': {_raise_se}\n"
        + f"        if type(getattr(globals()[{zx_key_src}][1], {_h_loads})).__name__ != 'builtin_function_or_method': {_raise_se}\n"
        + f"        {h_dck} = getattr(globals()[{zx_key_src}][2], {_h_decompress})\n"
        + f"        if type({h_dck}).__name__ not in ('builtin_function_or_method', 'function') or (type({h_dck}).__name__ == 'function' and {h_dck}({_canary_blob!r}) != {_canary_plain!r}): {_raise_se}\n"
        + _decoy_lines
        + _real_loads
        + f"        if getattr(__import__({_h_builtins}), {_h_exec}) is not {h_var_exec}: "
        f"{_raise_se}\n"
        f"        {h_var_exec}({h_var_code}, globals())\n"
        f"        globals().pop({zx_key_src}, None)\n"
        f"        globals().pop({cnt_key_src}, None)\n"
    )
    _bs_rng = _seeded_rng(build_seed, "shape::bootstrap")
    if enable_guard:
        _tok_line = (f"    {h_btok} = {anti_fn_trace}()\n"
                     f"    {anti_fn_hook}()\n")
    else:
        _tok_line = f"    {h_btok} = ''\n"
    _delattr_lines = "".join(
        f"del {payload_class_name}.{name}\n" for name in frag_attr_names)
    _load_line = '    print("Loading...")\n'
    _boot_body = (_load_line + _tok_line +
                  wd_start_line +
                  f"    {h_pl} = {payload_class_name}().{p_meth_get}({h_btok})\n"
                  f"    del {h_btok}\n" +
                  "".join(f"    {ln}" for ln in _delattr_lines.splitlines(keepends=True)) +
                  f"    {user_class_name}().{h_meth_run}({h_pl})\n")
    _boot_run = f"try:\n" + _boot_body
    _scrub_names = list(scrub_names) if scrub_names else []
    _scrub_names += [payload_class_name, user_class_name, h_pl]
    if enable_guard:
        _scrub_names += [anti_fn_trace, anti_fn_hook, anti_fn_extra]
        try:
            _scrub_names.append(wd_func)
        except NameError:
            pass
    def _finally_top_block() -> str:
        return ("finally:\n" + "".join(
            f"    globals().pop({nm!r}, None)\n" for nm in _scrub_names)
            + f"    globals().pop({zx_key_src}, None)\n"
            + f"    globals().pop({cnt_key_src}, None)\n"
            + "    __import__('gc').collect()\n")
    _finally_top = _finally_top_block()
    _bs_pick = _bs_rng.random()
    if _bs_pick < 0.25:
        bootstrap_src = (
            _tok_line + _boot_run +
            f"except Exception as {b_exc}: {_raise_se}\n"
            f"except KeyboardInterrupt: {_raise_se}\n"
            + _finally_top
        )
    elif _bs_pick < 0.5:
        bootstrap_src = (
            _tok_line + _boot_run +
            f"except BaseException: {_raise_se}\n"
            + _finally_top
        )
    elif _bs_pick < 0.75:
        _wbody = "".join(
            ("    " + ln if ln.strip() else ln)
            for ln in _boot_body.splitlines(keepends=True))
        bootstrap_src = (
            _tok_line +
            f"try:\n"
            f"    while True:\n{_wbody}"
            f"        break\n"
            f"except Exception as {b_exc}: {_raise_se}\n"
            f"except KeyboardInterrupt: {_raise_se}\n"
            + _finally_top
        )
    else:
        _inner = (_boot_run +
                  f"except Exception as {b_exc}: {_raise_se}\n"
                  f"except KeyboardInterrupt: {_raise_se}\n"
                  + _finally_top)
        _wrapped = "".join(
            ("    " + ln if ln.strip() else ln)
            for ln in _inner.splitlines(keepends=True))
        bootstrap_src = f"if True:\n{_wrapped}"
    noise_src = ""
    if cfg and cfg.enable_anti_decompli:
        try:
            _compact_arch = not bool(getattr(cfg, "enforce_ibe_band", True))
            noise_src = _decompiler_noise_source(
                build_seed, naming_hira,
                rounds=(3 if _compact_arch else 24)) + "\n"
        except Exception:
            noise_src = ""
    full_source = (
        (("\n" + noise_src) if noise_src else "")
        + (("\n" + anti_combo_funcs_src) if anti_combo_funcs_src else "")
        + (("\n" + watchdog_src) if watchdog_src else "")
        + (("\n" + anti_decompli_func_src) if anti_decompli_func_src else "")
        + "\n" + user_class_src
        + "\n" + payload_class_src
        + "\n" + bootstrap_src
    )
    return full_source, anti_combo_status, anti_decompli_status
@dataclass
class BuildReport:
    stages_run: List[str] = field(default_factory=list)
    identifier_map: Dict[str, str] = field(default_factory=dict)
    strings_protected: int = 0
    strings_skipped: int = 0
    string_fragments_generated: int = 0
    constants_protected: int = 0
    constants_skipped: int = 0
    structural_expansion_counts: Dict[str, int] = field(default_factory=dict)
    control_flow_straight_line: int = 0
    control_flow_if_chains: int = 0
    ast_nodes_before: int = 0
    ast_nodes_after: int = 0
    stmts_before: int = 0
    stmts_after: int = 0
    generated_helpers: int = 0
    rollbacks: int = 0
    marshal_payload_bytes: int = 0
    used_user_payload_arch: bool = False
    warnings: List[str] = field(default_factory=list)
    validation: Optional[ValidationResult] = None
    output_source: Optional[str] = None
    build_seed_used: Optional[int] = None
    input_size_bytes: int = 0
    output_size_bytes: int = 0
    expansion_ratio: float = 0.0
    ibe_band: str = ""
    size_profile: str = ""
    transformation_passes: List[str] = field(default_factory=list)
    ast_layers: int = 0
    generated_structures: int = 0
    skipped_unsafe_passes: List[str] = field(default_factory=list)
    more_obfuscation_status: bool = False
    anti_combo_status: Dict[str, bool] = field(default_factory=dict)
    anti_decompli_status: bool = False
    requests_protect_status: bool = False
    requests_literals_protected: int = 0
    string_strategy_counts: Dict[str, int] = field(default_factory=dict)
    constant_strategy_counts: Dict[str, int] = field(default_factory=dict)
    second_stage_ran: bool = False
    second_stage_rolled_back: bool = False
    build_seconds: float = 0.0
    rounds_configured: int = 0
    rounds_executed: int = 0
    rounds_reverted: int = 0
    peak_memory_bytes: int = 0
    vm_regions: int = 0
    vm_instructions: int = 0
    vm_virtualized_nodes: int = 0
    vm_fallback_nodes: int = 0
    hard_output_budget: int = 0
    size_governor_attempts: int = 0
    size_governor_escalations: int = 0
    stage_sizes: Dict[str, int] = field(default_factory=dict)
    tamper_response_status: bool = False
    scrub_names: list = field(default_factory=list)
    vm_const_pool: int = 0
    vm_serialized_words: int = 0
    output_floor: int = 0
    output_ceiling: int = 0
    output_band_status: str = ""
    boost_rounds: int = 0
    compression_level_used: int = -1
    round_sizes: List[int] = field(default_factory=list)
    pass_size_deltas: Dict[str, int] = field(default_factory=dict)
    cfg_metrics_before: Dict[str, int] = field(default_factory=dict)
    cfg_metrics_after: Dict[str, int] = field(default_factory=dict)
    literals_total: int = 0
    protection_density: float = 0.0
    size_efficiency: float = 0.0
    build_efficiency: float = 0.0
    manifest: Dict[str, Any] = field(default_factory=dict)
    dispatcher_mode: str = ""
    string_category_counts: Dict[str, int] = field(default_factory=dict)
    mapping_audit: Dict[str, str] = field(default_factory=dict)
    diminishing_returns_hit: bool = False
    verdict_reason: str = ""
    error_taxonomy: str = ""
    pipeline_plan: List[str] = field(default_factory=list)
    pipeline_plan_notes: List[str] = field(default_factory=list)
    source_hash: str = ""
    def log(self, module: str, message: str):
        self.warnings.append(f"[{module}] {message}")
    def finalize_metrics(self) -> None:
        def _ratio(part: int, whole: int) -> float:
            return min(1.0, part / whole) if whole > 0 else 0.0
        strings_cov = _ratio(
            self.strings_protected,
            max(1, self.strings_protected + self.strings_skipped))
        consts_cov = _ratio(
            self.constants_protected,
            max(1, self.constants_protected + self.constants_skipped))
        rename_cov = _ratio(len(self.identifier_map),
                            max(1, self.ast_nodes_before // 4))
        vm_cov = _ratio(self.vm_regions,
                        max(1, self.vm_regions + self.vm_fallback_nodes))
        cfg_count = self.control_flow_straight_line + self.control_flow_if_chains
        cfg_cov = _ratio(cfg_count,
                         max(1, self.cfg_metrics_before.get("functions", 1)))
        expand_cov = _ratio(sum(self.structural_expansion_counts.values()),
                            max(1, self.ast_nodes_after // 3))
        integ_cov = 1.0 if "INTEGRITY_METADATA" in self.stages_run else 0.0
        guard_cov = 1.0 if any(s.startswith("ANTI_COMBO") or
                               s == "RUNTIME_GUARD" for s in self.stages_run) else 0.0
        self.protection_density = round(
            0.20 * strings_cov + 0.18 * consts_cov + 0.14 * rename_cov
            + 0.16 * vm_cov + 0.12 * cfg_cov + 0.08 * expand_cov
            + 0.06 * integ_cov + 0.06 * guard_cov, 4)
        out_kb = max(0.001, self.output_size_bytes / 1024.0)
        self.size_efficiency = round(self.protection_density / out_kb, 6)
        build_s = max(0.001, self.build_seconds)
        self.build_efficiency = round(self.protection_density / build_s, 4)
    def build_manifest(self, config: "DragonConfig") -> Dict[str, Any]:
        import hashlib as _h
        out_h = ""
        if self.output_source:
            out_h = _h.sha256(self.output_source.encode("utf-8")).hexdigest()
        return {
            "manifest_version": MAPPING_SCHEMA_VERSION,
            "pipeline_schema": PIPELINE_SCHEMA_VERSION,
            "integrity_schema": INTEGRITY_SCHEMA_VERSION,
            "payload_format": PAYLOAD_FORMAT_VERSION,
            "profile": config.profile,
            "seed": self.build_seed_used,
            "seed_namespaces": SeedBookkeeper(
                self.build_seed_used or 0).as_dict(),
            "enabled_layers": [s for s in self.stages_run],
            "pipeline_plan": list(self.pipeline_plan),
            "source_size_bytes": self.input_size_bytes,
            "output_size_bytes": self.output_size_bytes,
            "output_hash_sha256": out_h,
            "source_hash_sha256": self.source_hash,
            "ibe_band": self.ibe_band,
            "band_status": self.output_band_status,
            "protection_density": self.protection_density,
            "verdict": self.output_band_status,
        }
    def report_dict(self, include_mapping: bool = False) -> Dict[str, Any]:
        d = {
            "schema": PIPELINE_SCHEMA_VERSION,
            "input_size_bytes": self.input_size_bytes,
            "output_size_bytes": self.output_size_bytes,
            "ast_nodes_before": self.ast_nodes_before,
            "ast_nodes_after": self.ast_nodes_after,
            "stmts_before": self.stmts_before,
            "stmts_after": self.stmts_after,
            "strings_protected": self.strings_protected,
            "strings_skipped": self.strings_skipped,
            "string_categories": self.string_category_counts,
            "constants_protected": self.constants_protected,
            "constants_skipped": self.constants_skipped,
            "identifiers_renamed": len(self.identifier_map),
            "vm_regions": self.vm_regions,
            "vm_instructions": self.vm_instructions,
            "vm_dispatch_mode": self.dispatcher_mode,
            "cfg_transforms": {
                "straight_line": self.control_flow_straight_line,
                "if_chains": self.control_flow_if_chains,
                "metrics_before": self.cfg_metrics_before,
                "metrics_after": self.cfg_metrics_after,
            },
            "pass_size_deltas": self.pass_size_deltas,
            "round_sizes": self.round_sizes,
            "generated_helpers": self.generated_helpers,
            "rollbacks": self.rollbacks,
            "boost_rounds": self.boost_rounds,
            "size_governor_attempts": self.size_governor_attempts,
            "size_governor_escalations": self.size_governor_escalations,
            "ibe_band": self.ibe_band,
            "output_floor": self.output_floor,
            "output_ceiling": self.output_ceiling,
            "band_status": self.output_band_status,
            "compression_level_used": self.compression_level_used,
            "protection_density": self.protection_density,
            "size_efficiency": self.size_efficiency,
            "build_efficiency": self.build_efficiency,
            "peak_memory_bytes": self.peak_memory_bytes,
            "build_seconds": round(self.build_seconds, 3),
            "warnings": list(self.warnings),
            "skipped_constructs": list(self.skipped_unsafe_passes),
            "stages_run": list(self.stages_run),
            "pipeline_plan": list(self.pipeline_plan),
            "pipeline_plan_notes": list(self.pipeline_plan_notes),
            "pipeline_stages_view": PipelinePlan.stage_view(self.stages_run),
            "source_hash_sha256": self.source_hash,
            "verdict": (self.validation.status_line()
                        if self.validation else "not validated"),
            "final_verdict": self.output_band_status,
            "verdict_reason": self.verdict_reason,
            "manifest": self.manifest,
        }
        if include_mapping:
            d["identifier_mapping"] = dict(self.identifier_map)
        return d
class DkhInputTooLargeError(DkhInputLimitError):
    CODE = "ZD-E006"
class SizeBoostRollback(Exception):
    pass
def build(source: str, config: DragonConfig, filename: str = "<dzydkh_input>",
          validate: bool = True, run_validation: bool = False,
          strict: bool = False) -> BuildReport:
    t_start = time.time()
    n_bytes = len(source.encode("utf-8"))
    if n_bytes <= 0:
        raise DkhInputTooLargeError(
            "Input is empty; Dkh only accepts non-empty Python sources.")
    if n_bytes > _max_input_bytes_for(config) > 0:
        raise DkhInputTooLargeError(
            f"Input is {_format_size(n_bytes)}; Dkh only accepts inputs up to "
            f"{_format_size(_max_input_bytes_for(config))}. Split the file and protect "
            f"the parts separately.")
    probe_tree = ast.parse(source, filename=filename)
    _retaliation_mode = str(
        getattr(config, "retaliation_scan", "refuse") or "off").lower()
    _retaliation_notes: List[str] = []
    if _retaliation_mode in ("refuse", "warn"):
        _ret_findings = scan_retaliation(probe_tree)
        for _sev, _ln, _msg in _ret_findings:
            _retaliation_notes.append(
                f"[RETALIATION_SCAN:{_sev}] line {_ln}: {_msg}")
        _ret_crit = [f for f in _ret_findings if f[0] == "critical"]
        if _ret_crit and _retaliation_mode == "refuse":
            raise DkhRetaliationRefusal(
                "refused: input contains retaliation patterns (self-delete / "
                f"browser-spam / BSOD): {len(_ret_crit)} critical finding(s); "
                f"first: {_ret_crit[0][2]}")
    n_nodes = sum(1 for _ in ast.walk(probe_tree))
    n_stmts = sum(1 for node in ast.walk(probe_tree) if isinstance(node, ast.stmt))
    tracemalloc_started_here = not tracemalloc.is_tracing()
    if tracemalloc_started_here:
        tracemalloc.start()
    mem_baseline, _ = tracemalloc.get_traced_memory()
    try:
        attempt = 0
        escalation = 0
        carried_logs: List[str] = []
        carried_logs.extend(_retaliation_notes)
        best_over: Optional[BuildReport] = None
        time_budget_hit = False
        report: Optional[BuildReport] = None
        while True:
            _budget = float(getattr(config, "max_build_seconds", 0) or 0)
            if _budget > 0 and (time.time() - t_start) >= _budget:
                time_budget_hit = True
                break
            try:
                report = _dzydkh_pipeline(
                    source, config, filename=filename,
                    validate=validate, run_validation=run_validation,
                    input_metrics=(n_bytes, n_nodes, n_stmts),
                    adaptive_attempt=attempt,
                    escalation=escalation,
                )
            except SyntaxError as e:
                raise DkhParseError(f"input/output parse failure: {e}") from e
            except DkhError:
                raise
            except RecursionError as e:
                raise DkhTransformError(
                    f"transform stage hit recursion limit: {e!r}") from e
            except MemoryError as e:
                raise DkhTransformError(
                    f"transform stage exhausted memory: {e!r}") from e
            except Exception as e:
                raise DkhTransformError(
                    "transform stage failed: "
                    + traceback.format_exc(limit=-4)) from e
            if carried_logs:
                report.warnings[:0] = carried_logs
            if report.output_band_status == "in-band":
                report.size_governor_attempts = attempt
                report.size_governor_escalations = escalation
                break
            if report.output_band_status == "over-ceiling":
                if best_over is None or report.output_size_bytes < best_over.output_size_bytes:
                    best_over = report
                if attempt >= max(0, int(config.max_build_retries)):
                    report.size_governor_attempts = attempt
                    break
                report.log("SIZE_GOVERNOR",
                           f"attempt {attempt}: {_format_size(report.output_size_bytes)} exceeds "
                           f"band ceiling {_format_size(report.output_ceiling)} — rebuilding reduced.")
                carried_logs.extend(report.warnings)
                attempt += 1
                continue
            if escalation >= max(0, int(config.max_escalation_rebuilds)):
                report.size_governor_attempts = attempt
                report.size_governor_escalations = escalation
                break
            report.log("SIZE_GOVERNOR",
                       f"escalation {escalation}: {_format_size(report.output_size_bytes)} below "
                       f"band floor {_format_size(report.output_floor)} after internal boost — "
                       f"rebuilding with escalated protection intensity.")
            carried_logs.extend(report.warnings)
            escalation += 1
        if report is None:
            raise DkhSizeGovernorError(
                f"Size governor wall-clock budget exceeded: "
                f"max_build_seconds={float(getattr(config, 'max_build_seconds', 0) or 0):.0f}s; "
                f"input {_format_size(n_bytes)}, no output produced.")
        if time_budget_hit:
            report.log("SIZE_GOVERNOR",
                       f"time budget exhausted after {time.time() - t_start:.1f}s "
                       f"(attempt={attempt}, escalation={escalation}) — keeping "
                       f"last measured output honestly as {report.output_band_status}.")
            report.size_governor_attempts = attempt
            report.size_governor_escalations = escalation
        if (report.output_band_status == "over-ceiling"
                and best_over is not None
                and best_over is not report):
            best_over.size_governor_attempts = attempt
            report = best_over
        _, mem_peak = tracemalloc.get_traced_memory()
        report.peak_memory_bytes = max(0, mem_peak - mem_baseline)
        report.build_seconds = time.time() - t_start
        if strict:
            if report.validation is not None and not report.validation.success:
                v = report.validation
                if v.executed is False:
                    raise DkhRuntimeError(
                        f"runtime validation failed: {v.error_message}")
                    raise DkhCompileError(
                    f"{v.error_stage} validation failed: {v.error_message}")
            if report.output_band_status != "in-band":
                    raise DkhSizeGovernorError(
                    f"{report.verdict_reason or report.output_band_status}",
                    report=report)
        return report
    finally:
        if tracemalloc_started_here:
            tracemalloc.stop()
def _dzydkh_pipeline(source: str, config: DragonConfig, filename: str = "<dzydkh_input>",
                    validate: bool = True, run_validation: bool = False,
                    input_metrics: Optional[Tuple[int, int, int]] = None,
                    adaptive_attempt: int = 0, escalation: int = 0) -> BuildReport:
    _t_start = time.time()
    report = BuildReport()
    report.input_size_bytes = len(source.encode("utf-8"))
    build_seed = config.seed if config.seed is not None else random.SystemRandom().randint(0, 2**32 - 1)
    report.build_seed_used = build_seed
    report.source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    _compact_mode = not bool(getattr(config, "enforce_ibe_band", True))
    tree = ast.parse(source, filename=filename)
    report.stages_run.append("PARSE_INPUT")
    _stripped_docs = strip_docstrings(tree)
    if _stripped_docs:
        report.log("COMMENT_POLICY", f"{_stripped_docs} docstring(s) removed from output.")
    report.literals_total = count_protectable_literals(tree)
    report.cfg_metrics_before = cfg_metrics(tree)
    if input_metrics is not None:
        _ib, report.ast_nodes_before, report.stmts_before = input_metrics
    else:
        report.ast_nodes_before = sum(1 for _ in ast.walk(tree))
        report.stmts_before = sum(1 for node in ast.walk(tree) if isinstance(node, ast.stmt))
    adaptive = AdaptiveExpansionController(
        report.input_size_bytes, report.ast_nodes_before, report.stmts_before,
        config, attempt=adaptive_attempt, escalation=escalation,
    )
    report.size_profile = adaptive.size_profile
    report.ibe_band = adaptive.band_label
    report.ast_layers = adaptive.max_rounds
    report.hard_output_budget = adaptive.output_ceiling
    report.more_obfuscation_status = config.more_obfuscation
    report.log("ADAPTIVE_EXPANSION", adaptive.describe())
    if config.enable_structural_expansion:
        try:
            _early_naming = RuneNaming(
                style="ascii", build_seed=derive_seed(build_seed, "helpers"))
            n_wrapped = ModuleEntryWrapPass(
                _early_naming, script_mode=bool(config.script_mode)).transform(
                tree, _seeded_rng(build_seed, "entry_wrap"))
            if n_wrapped:
                report.stages_run.append("MODULE_ENTRY_WRAP")
                report.log("STRUCTURAL",
                           "scope-simple module wrapped into a generated entry "
                           "function — real protection surface enabled.")
        except Exception as w_exc:
            report.log("STRUCTURAL", f"module_entry_wrap skipped: {w_exc!r}")
    scope_report: ScopeReport = analyze(tree, source_filename=filename)
    report.stages_run.append("ANALYZE")
    report.warnings.extend(scope_report.warnings)
    requests_detected = detect_requests_import(tree)
    report.requests_protect_status = bool(config.requests_protect and requests_detected)
    if config.requests_protect and not requests_detected:
        report.log("REQUESTS_PROTECT", "requests_protect enabled but `requests` not imported — no-op.")
    if report.requests_protect_status and not config.enable_integrity:
        config.enable_integrity = True
        report.log("REQUESTS_PROTECT", "integrity force-enabled (Request Protect arms tamper-evidence).")
    _src_identifiers: Set[str] = set()
    for _n in ast.walk(tree):
        if isinstance(_n, ast.Name):
            _src_identifiers.add(_n.id)
        elif isinstance(_n, ast.Attribute):
            _src_identifiers.add(_n.attr)
        elif isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            _src_identifiers.add(_n.name)
    naming = RuneNaming(style=config.identifier_style,
                        ascii_prefix=config.identifier_prefix,
                        build_seed=build_seed,
                        avoid=_src_identifiers)
    naming_cjk  = RuneNaming(style="cjk",      build_seed=build_seed + 1,
                             avoid=_src_identifiers)
    naming_hira = RuneNaming(style="hiragana",  build_seed=build_seed + 2,
                             avoid=_src_identifiers)
    def _track_stage_size(key: str) -> None:
        _fixup_ast_fields(tree)
        try:
            report.stage_sizes[key] = len(ast.unparse(tree).encode("utf-8"))
        except Exception as exc:
            report.log("SIZE_TRACK", f"{key}: measurement failed ({exc!r})")
    if config.enable_identifier_mangling:
        registry = mangle_with_naming(tree, scope_report, config, naming)
        report.identifier_map = registry.as_dict()
        _post_rename_ids: Set[str] = set()
        for _nn in ast.walk(tree):
            if isinstance(_nn, ast.Name):
                _post_rename_ids.add(_nn.id)
            elif isinstance(_nn, ast.arg):
                _post_rename_ids.add(_nn.arg)
            elif isinstance(_nn, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                _post_rename_ids.add(_nn.name)
            elif isinstance(_nn, ast.ExceptHandler) and _nn.name:
                _post_rename_ids.add(_nn.name)
            elif isinstance(_nn, ast.alias) and _nn.asname:
                _post_rename_ids.add(_nn.asname)
        naming._used_names.update(_post_rename_ids)
        naming_cjk._used_names.update(_post_rename_ids)
        naming_hira._used_names.update(_post_rename_ids)
        report.stages_run.append("RUNE_MANGLER")
        _track_stage_size("rune_mangler")
    else:
        report.log("PIPELINE", "Identifier mangling disabled — skipped.")
    if getattr(config, "enable_requests_forge", True) and (
            config.expansion_level in ("high", "extreme")
            or config.more_obfuscation):
        try:
            _rq_n = RequestsForgePass(
                naming, build_seed,
                coverage=min(0.9, 0.5 + 0.55 * 0.5)).transform(
                    tree, _seeded_rng(build_seed, "requests_forge_main"))
            if _rq_n:
                report.stages_run.append("REQUESTS_FORGE")
                report.structural_expansion_counts["requests_forge"] = \
                    report.structural_expansion_counts.get(
                        "requests_forge", 0) + _rq_n
                report.log("REQUESTS_FORGE",
                           f"{_rq_n} requests call site(s) forged "
                           f"(endpoint hidden, decoy headers, HMAC sign).")
        except Exception as _rq_fatal:
            report.log("REQUESTS_FORGE",
                       f"aborted — internal error (build continues): "
                       f"{_rq_fatal!r}")
    if config.enable_vm and int(adaptive_attempt) < 2:
        try:
            (vm_regions, vm_instrs, vm_virt_stmts, vm_fallback,
             vm_warnings, vm_stats) = apply_selective_vm_virtualization(
                 tree, build_seed, force_flat=_compact_mode,
                 scrub=report.scrub_names)
        except Exception as _vm_fatal:
            report.log("VM",
                       "virtualization ABORTED — unexpected internal error "
                       "(build continues WITHOUT VM):\n"
                       + traceback.format_exc(limit=-6))
            vm_regions = vm_instrs = vm_virt_stmts = vm_fallback = 0
            vm_warnings = [f"[SKIPPED] VM internal error: {_vm_fatal!r}"]
            vm_stats = {}
        report.vm_regions = vm_regions
        report.vm_instructions = vm_instrs
        report.vm_virtualized_nodes = vm_virt_stmts
        report.vm_fallback_nodes = vm_fallback
        report.vm_const_pool = vm_stats.get("const_pool", 0)
        report.vm_serialized_words = vm_stats.get("serialized_words", 0)
        report.dispatcher_mode = str(vm_stats.get("dispatch_mode", ""))
        report.warnings.extend(vm_warnings)
        report.skipped_unsafe_passes.extend(w for w in vm_warnings if "SKIPPED" in w)
        if vm_regions > 0:
            report.stages_run.append("VM_VIRTUALIZATION")
            report.generated_structures += 2
            report.log("VM", f"{vm_regions} function(s) virtualized "
                       f"({vm_instrs} instructions, permuted opcode ids, "
                       f"{vm_stats.get('const_pool', 0)} pooled constants, "
                       f"{vm_stats.get('serialized_words', 0)} flat halfwords); "
                       f"{vm_fallback} function(s) fell back to normal Python.")
        else:
            report.log("VM", f"selective virtualization attempted, 0 of "
                       f"{vm_fallback} candidate function(s) matched the supported "
                       f"subset — all left as normal Python (expected and honest).")
    elif getattr(config, "enable_vm", False):
        report.log("VM", "skipped on over-ceiling retry "
                        f"(attempt={int(adaptive_attempt)}): band-fit shed, "
                        "functions left as normal Python (semantics kept).")
    _unsafe_names: Set[str] = (
        scope_report.dynamic_referenced_names
        | scope_report.global_unsafe_names
        | scope_report.imported_names
        | scope_report.attribute_names
    )
    if config.enable_string_protection:
        try:
            _dedup_n = StringDedupPass(naming).transform(
                tree, _seeded_rng(build_seed, "string_dedup"))
        except Exception as dedup_exc:
            report.log("STRING_DEDUP",
                       f"aborted — internal error (build continues): "
                       f"{dedup_exc!r}")
            _dedup_n = 0
        if _dedup_n:
            report.stages_run.append("STRING_DEDUP")
            report.log("STRING_DEDUP", f"{_dedup_n} duplicate string site(s) collapsed.")
    if config.enable_structural_expansion:
        cfg_seed = DragonConfig(**{**config.__dict__, "seed": build_seed, "warnings": list(config.warnings)})
        gate = config.expansion_level in ("high", "extreme") or config.more_obfuscation
        expansion_counts, expansion_skips, rounds_info = run_structural_expansion(
            tree, cfg_seed, naming, unsafe_names=_unsafe_names,
            node_budget=adaptive.max_ast_nodes, rounds=adaptive.max_rounds,
            med_cap=adaptive.med_cap, junk_cases=adaptive.junk_cases,
            global_med_cap=adaptive.global_med_cap,
            force_extra_passes=config.more_obfuscation,
            size_limit_bytes=_band_structural_limit(config, adaptive),
            coverage=adaptive.coverage,
            dead_path_cap=(24 + 12 * escalation) if gate else 0,
            return_split_cap=(40 if gate else 0),
        )
        report.structural_expansion_counts.update(expansion_counts)
        report.warnings.extend(expansion_skips)
        report.pipeline_plan = list(rounds_info.get("plan", []))
        report.pipeline_plan_notes = [
            n for n in rounds_info.get("plan_notes", []) if isinstance(n, str)]
        if report.pipeline_plan_notes:
            report.warnings.extend(report.pipeline_plan_notes)
        report.skipped_unsafe_passes.extend(s for s in expansion_skips if "SKIPPED" in s)
        report.rounds_configured = rounds_info["configured"]
        report.rounds_executed = rounds_info["executed"]
        report.rounds_reverted = rounds_info["reverted"]
        report.round_sizes = rounds_info.get("round_sizes", [])
        stage_label = "AST_STRUCTURAL_EXPANSION"
        if config.enable_globals_storage and config.expansion_level == "extreme":
            stage_label += "+GLOBALS_STORAGE"
        if config.enable_memory_error_dispatch and gate:
            stage_label += "+MEM_DISPATCH"
        if config.enable_opaque_predicates and gate:
            stage_label += "+OPAQUE"
        if getattr(config, "enable_chain_links", True) and gate:
            stage_label += "+CHAIN"
        if getattr(config, "enable_handler_embed", True) and gate:
            stage_label += "+HANDLER"
        if getattr(config, "enable_match_flatten", True) and gate:
            stage_label += "+FLAT"
        if getattr(config, "enable_dead_bloat", True) and gate:
            stage_label += "+BLOAT"
        if getattr(config, "enable_decompiler_traps", True) and gate:
            stage_label += "+TRAPS"
        if config.enable_boolean_algebra:
            stage_label += "+BOOL_ALGEBRA"
        if config.more_obfuscation:
            stage_label += "+MORE_OBF"
        report.stages_run.append(stage_label)
        _track_stage_size("structural_expansion")
        total_expanded = sum(expansion_counts.values())
        report.log("STRUCTURAL_EXPANSION",
                   f"{total_expanded} node(s) transformed across "
                   f"{rounds_info['executed']}/{rounds_info['configured']} round(s) "
                   f"committed ({rounds_info['reverted']} reverted; budget="
                   f"{adaptive.max_ast_nodes}).")
    else:
        report.log("PIPELINE", "AST structural expansion disabled — skipped.")
    if config.enable_string_protection:
        try:
            _fs_conv, _fs_skip = run_fstring_split(tree)
            if _fs_conv:
                report.stages_run.append("FSTRING_SPLIT")
                report.structural_expansion_counts["fstring_split"] = \
                    report.structural_expansion_counts.get(
                        "fstring_split", 0) + _fs_conv
                report.log("FSTRING_SPLIT",
                           f"{_fs_conv} f-string(s) converted to "
                           f"'<fmt>'.format(...) call sites "
                           f"({_fs_skip} left native).")
        except Exception as fs_exc:
            report.log("FSTRING_SPLIT",
                       f"aborted — internal error (build continues): "
                       f"{fs_exc!r}")
    if getattr(config, "enable_int_pool", False) \
            and config.enable_constant_protection:
        _ip_gate = (config.expansion_level in ("high", "extreme")
                    or config.more_obfuscation)
        if _ip_gate:
            try:
                _ip_n = IntPoolIndirectionPass(
                    naming, build_seed,
                    coverage=min(0.99, (0.90 + 0.04 * escalation)
                                 * (0.7 ** max(0, int(adaptive_attempt)))),
                ).transform(tree, _seeded_rng(build_seed, "int_pool_main"))
            except Exception as _ip_fatal:
                report.log("INT_POOL",
                           f"aborted — internal error (build continues): "
                           f"{_ip_fatal!r}")
                _ip_n = 0
            if _ip_n:
                report.stages_run.append("INT_POOL")
                report.structural_expansion_counts["int_pool"] = \
                    report.structural_expansion_counts.get("int_pool", 0) + _ip_n
                report.log("INT_POOL",
                           f"{_ip_n} large integer literal(s) rerouted through a "
                           f"seed-shuffled XOR-masked module pool.")
    if getattr(config, "enable_int_hide", True) \
            and config.enable_constant_protection:
        _ih_gate = (config.expansion_level in ("high", "extreme")
                    or config.more_obfuscation)
        if _ih_gate:
            try:
                _ih_n = IntHidePass(
                    naming, build_seed,
                    coverage=min(0.95, (0.85 + 0.02 * escalation)
                                 * (0.7 ** max(0, int(adaptive_attempt)))),
                ).transform(tree, _seeded_rng(build_seed, "int_hide_main"))
            except Exception as _ih_fatal:
                report.log("INT_HIDE",
                           f"aborted — internal error (build continues): "
                           f"{_ih_fatal!r}")
                _ih_n = 0
            if _ih_n:
                report.stages_run.append("INT_HIDE")
                report.structural_expansion_counts["int_hide"] = \
                    report.structural_expansion_counts.get("int_hide", 0) + _ih_n
                report.log("INT_HIDE",
                           f"{_ih_n} integer literal(s) rewritten as "
                           f"H(BIG+v) with H(x)=int(x-BIG).")
    if config.enable_constant_protection:
        c_protected, c_skipped, c_strategy = protect_constants(
            tree,
            DragonConfig(**{**config.__dict__, "seed": build_seed, "warnings": list(config.warnings)}),
            int_chain_max_depth=2 + min(2, escalation),
            bool_coverage=min(0.95, 0.7 + 0.1 * escalation),
        )
        report.constants_protected = c_protected
        report.constants_skipped = c_skipped
        report.constant_strategy_counts = c_strategy
        report.stages_run.append("CONSTANT_PROTECTOR")
        _track_stage_size("constant_protection")
        report.log("CONSTANT_PROTECTOR",
                   f"{c_protected} protected (arithmetic={c_strategy.get('arithmetic',0)}, "
                   f"xor={c_strategy.get('xor',0)}, chain={c_strategy.get('chain',0)}, "
                   f"bool={c_strategy.get('bool',0)}), {c_skipped} skipped.")
    else:
        report.log("PIPELINE", "Constant protection disabled — skipped.")
    if report.requests_protect_status:
        req_count = protect_requests_literals(
            tree, DragonConfig(**{**config.__dict__, "seed": build_seed, "warnings": list(config.warnings)}), naming
        )
        report.requests_literals_protected = req_count
        report.stages_run.append("REQUESTS_PROTECT")
        report.log("REQUESTS_PROTECT", f"{req_count} URL/credential-like literal(s) force-protected.")
    if config.enable_string_protection:
        cfg_with_seed = DragonConfig(**{**config.__dict__, "seed": build_seed, "warnings": list(config.warnings)})
        lvl = config.expansion_level
        use_mixed = lvl in ("high", "extreme") or config.more_obfuscation
        if use_mixed:
            protected, skipped, strategy_counts, cat_counts = protect_mixed(
                tree, cfg_with_seed, naming)
            report.string_strategy_counts = strategy_counts
            report.string_category_counts = cat_counts
            report.stages_run.append("MIXED_STRING_ROUTER")
            report.log("MIXED_STRING_ROUTER",
                       f"{protected} protected (serpent={strategy_counts.get('serpent',0)}, "
                       f"chr_arithmetic={strategy_counts.get('chr_arithmetic',0)}, "
                       f"ember_table={strategy_counts.get('ember_table',0)}, "
                       f"interleaved_hex={strategy_counts.get('interleaved_hex',0)}, "
                       f"affine_char={strategy_counts.get('affine_char',0)}), {skipped} skipped.")
        else:
            protected, skipped = protect(tree, cfg_with_seed)
            report.stages_run.append("EMBER_CIPHER")
        report.strings_protected = protected
        report.strings_skipped = skipped
        _track_stage_size("string_protection")
    else:
        report.log("PIPELINE", "String protection disabled — skipped.")
    if config.enable_control_flow:
        try:
            sl_count, if_count = flatten_eligible_blocks(tree, build_seed)
        except Exception as _wyrm_fatal:
            report.log("WYRM_FLOW",
                       f"aborted — internal error (build continues): "
                       f"{_wyrm_fatal!r}")
            sl_count = if_count = 0
        report.control_flow_straight_line = sl_count
        report.control_flow_if_chains = if_count
        report.stages_run.append("WYRM_FLOW")
        _track_stage_size("wyrm_flow")
        report.log("WYRM_FLOW",
                   f"{sl_count} straight-line + {if_count} if-chain block(s) transformed "
                   f"(permuted state ids).")
        snap_cfg = None
        try:
            snap_cfg = _fast_ast_clone(tree.body)
            wd = WhileFormDiversification(
                build_seed, coverage=0.45 + 0.1 * min(3, escalation))
            bn = BranchNormalizationPass(
                build_seed + 5, coverage=0.35 + 0.1 * min(3, escalation))
            n_wd = wd.transform(tree, _seeded_rng(build_seed, "cfg::wd"))
            n_bn = bn.transform(tree, _seeded_rng(build_seed, "cfg::bn"))
            n_tg = n_cs = 0
            if _compact_mode:
                n_tg = TautologyGuardPass(
                    build_seed + 33, coverage=0.95, cap=40).transform(
                        tree, _seeded_rng(build_seed, "cfg::compact_tg"))
                n_cs = ConditionSwapPass(
                    build_seed + 34, coverage=0.5, cap=20).transform(
                        tree, _seeded_rng(build_seed, "cfg::compact_cs"))
            ast.fix_missing_locations(tree)
            compile(tree, "<dzydkh_cfg_check>", "exec")
            if n_wd:
                report.structural_expansion_counts["while_diversify"] = \
                    report.structural_expansion_counts.get("while_diversify", 0) + n_wd
            if n_bn:
                report.structural_expansion_counts["branch_normalize"] = \
                    report.structural_expansion_counts.get("branch_normalize", 0) + n_bn
            if n_tg:
                report.structural_expansion_counts["tautology_guard_compact"] = \
                    report.structural_expansion_counts.get("tautology_guard_compact", 0) + n_tg
            if n_cs:
                report.structural_expansion_counts["condition_swap_compact"] = \
                    report.structural_expansion_counts.get("condition_swap_compact", 0) + n_cs
            report.cfg_metrics_after = cfg_metrics(tree)
            report.log("CFG_ENGINE",
                       f"while-diversify x{n_wd}, branch-normalize x{n_bn}"
                       + (f", tautology-guard x{n_tg}, condition-swap x{n_cs} (compact)"
                          if _compact_mode else "") + ".")
        except Exception as cfg_exc:
            if snap_cfg is not None:
                tree.body[:] = snap_cfg
                ast.fix_missing_locations(tree)
            report.rollbacks += 1
            report.cfg_metrics_after = dict(report.cfg_metrics_before)
            report.log("CFG_ENGINE", f"rolled back: {cfg_exc!r}")
    if config.enable_structural_expansion and adaptive.second_stage_rounds > 0:
        stage1_snapshot_body = _fast_ast_clone(tree.body)
        try:
            extra_passes: List[TransformationPass] = []
            if config.enable_opaque_predicates:
                extra_passes.append(OpaquePredicatePass(config.expansion_level, build_seed + 97))
            if config.enable_builtins_indirection or config.more_obfuscation:
                extra_passes.append(BuiltinsIndirectionPass(naming, build_seed + 97))
            if extra_passes:
                already_used = sum(report.structural_expansion_counts.values())
                remaining_budget = max(0, adaptive.max_ast_nodes - already_used)
                if remaining_budget > 0:
                    stage2_manager = ASTPassManager(
                        extra_passes, adaptive.second_stage_rounds, build_seed + 97, remaining_budget
                    )
                    stage2_counts, stage2_skips = stage2_manager.run(tree)
                    ast.fix_missing_locations(tree)
                    compile(tree, "<dzydkh_stage2_check>", "exec")
                    for k, v in stage2_counts.items():
                        report.structural_expansion_counts[k] = \
                            report.structural_expansion_counts.get(k, 0) + v
                    report.warnings.extend(stage2_skips)
                    report.skipped_unsafe_passes.extend(s for s in stage2_skips if "SKIPPED" in s)
                    report.second_stage_ran = True
                    report.stages_run.append("RE_OBFUSCATION_STAGE2")
                    report.log("RE_OBFUSCATION",
                               f"stage 2: {sum(stage2_counts.values())} additional node(s) "
                               f"across {adaptive.second_stage_rounds} round(s).")
        except Exception as _stage2_exc:
            tree.body[:] = stage1_snapshot_body
            ast.fix_missing_locations(tree)
            report.second_stage_rolled_back = True
            report.rollbacks += 1
            report.log("RE_OBFUSCATION", f"stage 2 rolled back: {_stage2_exc!r}")
    if (config.enable_structural_expansion
            and getattr(config, "enable_module_maze", True)
            and config.expansion_level in ("high", "extreme")):
        maze_snap = _fast_ast_clone(tree.body)
        try:
            nseg, maze_names = apply_module_maze(tree, naming_hira, build_seed)
            ast.fix_missing_locations(tree)
            compile(tree, "<dzydkh_maze_check>", "exec")
            if nseg:
                report.structural_expansion_counts["module_maze"] = nseg
                report.stages_run.append("MODULE_MAZE")
                report.scrub_names.extend(maze_names)
                report.log("MODULE_MAZE",
                           f"{nseg} top-level segment(s) in staged exec maze.")
        except Exception as maze_exc:
            tree.body[:] = maze_snap
            ast.fix_missing_locations(tree)
            report.rollbacks += 1
            report.log("MODULE_MAZE", f"rolled back: {maze_exc!r}")
    report.ast_nodes_after = sum(1 for _ in ast.walk(tree))
    report.stmts_after = sum(1 for node in ast.walk(tree) if isinstance(node, ast.stmt))
    _fixup_ast_fields(tree)
    body_source = ast.unparse(tree)
    report.stages_run.append("CODEGEN")
    _use_arch = (
        config.enable_user_payload_arch
        and config.enable_marshal_runtime
        and (config.expansion_level in ("high", "extreme") or config.more_obfuscation)
    )
    if _compact_mode:
        header_and_guard = _compact_header_and_guard(config.username, config.target_python_version, build_seed)
    else:
        header_and_guard = build_header_and_guard(config.username, config.target_python_version, build_seed)
    ast.parse(header_and_guard)
    if _compact_mode:
        report.stages_run.append("COMPACT_HEADER")
        report.log("COMPACT", "50-line budget: compact 3-line header + flat VM + ;-packing.")
    anti_combo_status: Dict[str, bool] = {}
    anti_decompli_status = False
    report.tamper_response_status = False
    if config.enable_tamper_response:
        report.tamper_response_status = True
        report.log("TAMPER_RESPONSE",
                   "limited retaliation armed (20 tabs max, 0.5s apart, "
                   "integrity-failure only).")
    if config.enable_integrity:
        _early_seal = _integrity_boundary_names(build_seed)
        if _early_seal not in report.scrub_names:
            report.scrub_names.append(_early_seal)
    def _assemble_protected_output(program_text: str) -> str:
        program_text = _tidy_blank_lines(program_text)
        hh = _compute_body_checksum(header_and_guard)
        seal_name = _integrity_boundary_names(build_seed)
        if seal_name not in report.scrub_names:
            report.scrub_names.append(seal_name)
        tamper_src = (_tamper_response_source(config.tamper_url, build_seed) + "\n") \
            if report.tamper_response_status else ""
        prelude = tamper_src + _integrity_verifier_source(
            report.tamper_response_status, seal_name, build_seed,
            getattr(config, "tamper_url", ""),
            _tamper_func_name(build_seed) if report.tamper_response_status else None)
        ast.parse(prelude)
        tail = prelude + "\n" + program_text
        tail = _tidy_blank_lines(tail)
        if not tail.endswith("\n"):
            tail += "\n"
        bh = _compute_body_checksum(tail)
        return header_and_guard + f"{seal_name} = {(hh, bh)!r}\n" + tail
    floor = adaptive.output_floor
    ceiling = adaptive.output_ceiling
    report.output_floor = floor
    report.output_ceiling = ceiling
    report.hard_output_budget = ceiling
    arch_state = {"used": bool(_use_arch), "payload_len": 0}
    report.stage_sizes["post_codegen_ast"] = len(body_source.encode("utf-8"))
    def _materialize(compression: int):
        def _prepack(t: str) -> str:
            packed, did = _pack_preserving_ast(t)
            if did:
                report.log("COMPACT",
                           f"pre-packed arch source to {len(packed.splitlines())} lines (hash-safe).")
            return packed
        if arch_state["used"]:
            payload_src = body_source
            if getattr(config, "enable_stream_decrypt", False):
                try:
                    payload_src, _nstream = _stream_split_source(
                        body_source, build_seed, naming_hira, compression,
                        scrub=report.scrub_names)
                except Exception as _serr:
                    report.log("STREAM_DECRYPT",
                               f"splitter failed ({_serr!r}); single-vault fallback.")
                    payload_src = body_source
                    _nstream = 0
                else:
                    report.log("STREAM_DECRYPT",
                               f"{_nstream} function(s) in per-function vaults.")
                    report.stages_run.append("STREAM_DECRYPT")
            payload_b85 = _build_payload_bytes(payload_src, filename,
                                                compression_level=compression,
                                                comp=_envelope_poly(build_seed).get("comp", "zlib"))
            arch_state["payload_len"] = len(payload_b85)
            arch_src, acs, ads_ = _make_output_architecture(
                payload_b85=payload_b85,
                username=config.username,
                naming_cjk=naming_cjk,
                naming_hira=naming_hira,
                build_seed=build_seed,
                enable_guard=config.enable_runtime_guard,
                target_version=config.target_python_version,
                config=config,
                scrub_names=report.scrub_names,
            )
            text = (_assemble_protected_output(_prepack(arch_src))
                    if config.enable_integrity else header_and_guard + "\n" + arch_src)
            return text, acs, ads_
        text = (_assemble_protected_output(_prepack(body_source))
                if config.enable_integrity else header_and_guard + "\n" + body_source)
        return text, {}, False
    def _band_search():
        nonlocal _use_arch
        results = []
        for lvl in (9, 6, 3, 1, 0):
            try:
                txt, acs_l, ads_l = _materialize(lvl)
            except Exception as exc:
                report.log("SIZE_GOVERNOR", f"band search L{lvl} failed: {exc!r}")
                continue
            sz = len(txt.encode("utf-8"))
            results.append((lvl, sz, txt, acs_l, ads_l))
            report.log("SIZE_GOVERNOR",
                       f"band search: compression L{lvl} -> {_format_size(sz)} "
                       f"(floor {_format_size(floor)}).")
            if sz >= floor:
                return min([r for r in results if r[1] >= floor],
                           key=lambda r: (r[1], -r[0]))
        if not results and arch_state["used"]:
            report.warnings.append(
                "USER/PAYLOAD arch failed at every level; falling back to flat output."
            )
            arch_state["used"] = False
            _use_arch = False
            txt = (_assemble_protected_output(body_source)
                   if config.enable_integrity else header_and_guard + "\n" + body_source)
            results.append((0, len(txt.encode("utf-8")), txt, {}, False))
        if not results:
            return None
        in_band = [r for r in results if r[1] >= floor]
        if in_band:
            return min(in_band, key=lambda r: (r[1], -r[0]))
        if all(r[1] > ceiling for r in results):
            return min(results, key=lambda r: r[1])
        return max(results, key=lambda r: r[1])
    chosen = _band_search()
    if chosen is None:
        raise RuntimeError("output materialization failed at every compression level")
    comp_used, out_size, output_source = chosen[0], chosen[1], chosen[2]
    if chosen[3]:
        anti_combo_status = chosen[3]
    if chosen[4]:
        anti_decompli_status = True
    if out_size < floor and config.enable_structural_expansion:
        fill_round = [0]
        def _structural_round() -> int:
            fr = fill_round[0] + 1
            fill_round[0] = fr
            run_structural_expansion(
                tree, cfg_seed, naming, unsafe_names=_unsafe_names,
                node_budget=adaptive.max_ast_nodes,
                rounds=1,
                med_cap=min(12, adaptive.med_cap + fr),
                junk_cases=max(1, adaptive.junk_cases),
                global_med_cap=adaptive.global_med_cap * (fr + 1),
                force_extra_passes=True,
                size_limit_bytes=None,
                coverage=min(0.95, adaptive.coverage + 0.15 * fr),
                dead_path_cap=24 + 12 * fr,
                return_split_cap=40,
            )
            if config.enable_constant_protection:
                protect_constants(
                    tree,
                    DragonConfig(**{**config.__dict__,
                                    "seed": build_seed + 31 + fr,
                                    "warnings": list(config.warnings)}),
                    int_chain_max_depth=min(4, 3 + fr // 2),
                    bool_coverage=0.9,
                )
            if config.enable_control_flow:
                flatten_eligible_blocks(tree, build_seed + 40 + fr)
            return 1
        strategies: List[Tuple[str, Callable[[], int]]] = []
        if config.enable_memory_error_dispatch:
            strategies.append(("mem_dispatch+", lambda: MemoryErrorDispatcherPass(
                naming, config.expansion_level,
                cap_override=min(6, adaptive.med_cap + 2),
                junk_override=max(1, adaptive.junk_cases),
                global_cap=max(30, adaptive.global_med_cap),
            ).transform(tree, _seeded_rng(build_seed, "grow::med"))))
        if config.enable_opaque_predicates:
            strategies.append(("predicate_chain", lambda: OpaquePredicatePass(
                config.expansion_level, build_seed + 11, style="chain").transform(
                    tree, _seeded_rng(build_seed, "grow::opaque"))))
        strategies.append(("dead_path", lambda: DeadPathPass(
            naming, build_seed + 12, cap=60).transform(
                tree, _seeded_rng(build_seed, "grow::dp"))))
        strategies.append(("return_split", lambda: ReturnSplitPass(
            naming, cap=80).transform(tree, _seeded_rng(build_seed, "grow::rs"))))
        strategies.append(("call_indirection", lambda: CallIndirectionPass(
            naming, build_seed + 13, unsafe_names=_unsafe_names).transform(
                tree, _seeded_rng(build_seed, "grow::ci"))))
        strategies.append(("import_indirection", lambda: ImportIndirectionPass(
            naming, build_seed + 14).transform(
                tree, _seeded_rng(build_seed, "grow::ii"))))
        strategies.append(("int_pool+", lambda: IntPoolIndirectionPass(
            naming, build_seed + 21, coverage=0.99, min_abs=64).transform(
                tree, _seeded_rng(build_seed, "grow::ip"))))
        strategies.append(("lambda_thunk+", lambda: LambdaThunkPass(
            naming, build_seed + 22, coverage=0.6, cap=200).transform(
                tree, _seeded_rng(build_seed, "grow::lt"))))
        strategies.append(("tautology_guard+", lambda: TautologyGuardPass(
            build_seed + 23, coverage=0.85, cap=300).transform(
                tree, _seeded_rng(build_seed, "grow::tg"))))
        strategies.append(("condition_swap+", lambda: ConditionSwapPass(
            build_seed + 24, coverage=0.8, cap=250).transform(
                tree, _seeded_rng(build_seed, "grow::cs"))))
        strategies.append(("if_conjunct_split+", lambda: IfConjunctSplitPass(
            build_seed + 25, coverage=0.85, cap=200).transform(
                tree, _seeded_rng(build_seed, "grow::ics"))))
        strategies.append(("entry_guard+", lambda: EntryGuardPass(
            build_seed + 26, coverage=0.9, cap=180).transform(
                tree, _seeded_rng(build_seed, "grow::eg"))))
        strategies.append(("zero_div+", lambda: ZeroDivStatementWrapPass(
            naming, cap=min(8, max(2, adaptive.med_cap))).transform(
                tree, _seeded_rng(build_seed, "grow::zd"))))
        if config.enable_boolean_algebra:
            strategies.append(("boolean_algebra+", lambda: BooleanAlgebraPass(
                apply_probability=0.85).transform(
                    tree, _seeded_rng(build_seed, "grow::ba"))))
        strategies.append(("while_diversify+", lambda: WhileFormDiversification(
            build_seed + 15, coverage=0.8).transform(
                tree, _seeded_rng(build_seed, "grow::wd"))))
        strategies.append(("for_to_while+", lambda: ForToWhilePass(
            build_seed + 17, coverage=0.85).transform(
                tree, _seeded_rng(build_seed, "grow::fw"))))
        strategies.append(("branch_normalize+", lambda: BranchNormalizationPass(
            build_seed + 16, coverage=0.7).transform(
                tree, _seeded_rng(build_seed, "grow::bn"))))
        strategies.append(("structural_round+", _structural_round))
        def _apply_strategy(s_name: str, s_fn: Callable[[], int]) -> bool:
            nonlocal body_source, output_source, out_size
            snap = _fast_ast_clone(tree.body)
            prev_body = body_source
            try:
                n_changed = s_fn()
                if n_changed <= 0:
                    return False
                ast.fix_missing_locations(tree)
                compile(tree, "<dzydkh_grow_check>", "exec")
                body_source = ast.unparse(tree)
                new_out, acs_g, ads_g = _materialize(comp_used)
                nsize = len(new_out.encode("utf-8"))
                if nsize > ceiling:
                    raise SizeBoostRollback()
                if nsize <= out_size:
                    raise SizeBoostRollback()
                output_source = new_out
                out_size = nsize
                delta = max(0, len(body_source.encode("utf-8"))
                             - len(prev_body.encode("utf-8")))
                report.pass_size_deltas[s_name] = \
                    report.pass_size_deltas.get(s_name, 0) + delta
                if acs_g:
                    anti_combo_status_local.update(acs_g)
                if ads_g:
                    nonlocal_anti_decompli[0] = True
                return True
            except SizeBoostRollback:
                tree.body[:] = snap
                ast.fix_missing_locations(tree)
                body_source = prev_body
                report.rollbacks += 1
                return False
            except Exception as g_exc:
                tree.body[:] = snap
                ast.fix_missing_locations(tree)
                body_source = prev_body
                report.rollbacks += 1
                report.log("SIZE_GOVERNOR",
                           f"grow '{s_name}' failed ({g_exc!r}); rolled back.")
                return False
        anti_combo_status_local: Dict[str, bool] = dict(anti_combo_status)
        nonlocal_anti_decompli = [anti_decompli_status]
        boost_cap = min(60, config.max_boost_rounds * (1 + 2 * max(1, escalation)))
        max_sweeps = 2 + escalation
        sweep = 0
        ladder_idx = 0
        while (out_size < floor
               and report.boost_rounds < boost_cap
               and sweep < max_sweeps):
            s_name, s_fn = strategies[ladder_idx % len(strategies)]
            ladder_idx += 1
            if ladder_idx % len(strategies) == 0:
                sweep += 1
                strategies.sort(
                    key=lambda t: 0 if t[0].startswith("structural") else 1)
            if _apply_strategy(s_name, s_fn):
                report.boost_rounds += 1
                report.log("SIZE_GOVERNOR",
                           f"grow '{s_name}' -> {_format_size(out_size)} "
                           f"(floor {_format_size(floor)}, "
                           f"sweep {sweep + 1}/{max_sweeps}).")
            else:
                report.log("SIZE_GOVERNOR",
                           f"grow '{s_name}' yielded no committed growth "
                           f"(sweep {sweep + 1}/{max_sweeps}).")
        if out_size < floor:
            report.diminishing_returns_hit = True
            report.log("SIZE_GOVERNOR",
                       f"growth stopped: sweeps={max_sweeps}, "
                       f"boost={report.boost_rounds}/{boost_cap} — floor "
                       f"unreached; no junk filler applied.")
        anti_combo_status = anti_combo_status_local
        anti_decompli_status = anti_decompli_status or nonlocal_anti_decompli[0]
        chosen2 = _band_search()
        if chosen2 is not None and chosen2[1] >= floor and chosen2[1] <= ceiling:
            comp_used, out_size, output_source = chosen2[0], chosen2[1], chosen2[2]
            if chosen2[3]:
                anti_combo_status = chosen2[3]
            if chosen2[4]:
                anti_decompli_status = True
    report.compression_level_used = comp_used
    report.marshal_payload_bytes = arch_state["payload_len"]
    if arch_state["used"]:
        report.used_user_payload_arch = True
        report.stages_run.append("USER_PAYLOAD_ARCH")
        report.generated_helpers += 2
        report.generated_structures += 2
        if config.enable_runtime_guard:
            report.stages_run.append("RUNTIME_GUARD")
            report.generated_helpers += 1
            report.generated_structures += 1
        if anti_decompli_status:
            report.stages_run.append("ANTI_DECOMPLI")
            report.generated_structures += 1
            report.log("ANTI_DECOMPLI",
                       "independent Layer 3 active — payload reconstructed via a "
                       "standalone module-level function, interleaved buckets.")
        if config.anti_combo:
            active_modules = [k for k, v in anti_combo_status.items() if v]
            if active_modules:
                report.stages_run.append("ANTI_COMBO:" + "+".join(active_modules))
                report.generated_structures += len(active_modules)
                report.log("ANTI_COMBO", f"active modules: {', '.join(active_modules)}")
        if report.boost_rounds:
            report.stages_run.append(f"BOOST x{report.boost_rounds}")
    if config.enable_integrity:
        report.stages_run.append("INTEGRITY_METADATA")
    if not config.enable_integrity:
        report.log("PIPELINE", "Integrity checksum disabled — skipped.")
    report.anti_combo_status = anti_combo_status
    report.anti_decompli_status = anti_decompli_status
    output_source = _tidy_blank_lines(output_source)
    if _compact_mode and not config.enable_integrity:
        try:
            _pre_ast = ast.parse(output_source)
            _pre_dump = ast.dump(_pre_ast)
            _packed = _pack_compact_lines(output_source)
            _post_ast = ast.parse(_packed)
            if ast.dump(_post_ast) == _pre_dump:
                output_source = _packed
                report.stages_run.append("COMPACT_PACK")
                report.log("COMPACT",
                           f"packed to {len(output_source.splitlines())} lines "
                           f"(50-line budget, AST-identical).")
            else:
                report.log("COMPACT", "pack AST mismatch — kept unpacked output.")
        except Exception as _pack_exc:
            report.log("COMPACT", f"pack skipped ({_pack_exc!r}); kept unpacked output.")
        _nlines = len(output_source.splitlines())
        if _nlines > 50:
            report.log("COMPACT",
                       f"WARNING: compact output is {_nlines} lines (>50); "
                       f"budget exceeded — further packing needed.")
    ast.parse(output_source)
    report.stages_run.append("HEADER_INJECT")
    report.output_source = output_source
    report.output_size_bytes = len(output_source.encode("utf-8"))
    if report.output_size_bytes > report.output_ceiling:
        report.output_band_status = "over-ceiling"
        report.verdict_reason = (
            f"output {_format_size(report.output_size_bytes)} exceeds ceiling "
            f"{_format_size(report.output_ceiling)}")
        report.log("SIZE_GOVERNOR",
                   f"FINAL {_format_size(report.output_size_bytes)} EXCEEDS ceiling "
                   f"{_format_size(report.output_ceiling)}.")
    elif report.output_size_bytes < report.output_floor:
        report.output_band_status = "under-floor"
        report.verdict_reason = (
            f"output {_format_size(report.output_size_bytes)} below floor "
            f"{_format_size(report.output_floor)} after {report.boost_rounds} grow "
            f"round(s); strategies exhausted="
            f"{report.diminishing_returns_hit}; budget exhausted; no junk filler applied")
        report.log("SIZE_GOVERNOR",
                   f"FINAL {_format_size(report.output_size_bytes)} below floor "
                   f"{_format_size(report.output_floor)} after {report.boost_rounds} "
                   f"grow round(s) — budget exhausted; no junk filler applied.")
    else:
        report.output_band_status = "in-band"
        report.verdict_reason = (
            f"output {_format_size(report.output_size_bytes)} inside band "
            f"[{_format_size(report.output_floor)}.."
            f"{_format_size(report.output_ceiling)}]")
    report.expansion_ratio = report.output_size_bytes / max(1, report.input_size_bytes)
    report.transformation_passes = list(report.stages_run)
    if config.audit_mapping:
        report.mapping_audit = dict(report.identifier_map)
    if validate:
        result = validate_source(output_source, filename="<dzydkh_output>", run=run_validation)
        report.validation = result
        report.stages_run.append("VALIDATE")
    report.build_seconds = time.time() - _t_start
    report.finalize_metrics()
    report.manifest = report.build_manifest(config)
    return report
class DkhCliError(Exception):
    pass
_HAS_COLOR = None
def _has_color() -> bool:
    global _HAS_COLOR
    if _HAS_COLOR is not None:
        return _HAS_COLOR
    if os.environ.get("NO_COLOR"):
        _HAS_COLOR = False
        return _HAS_COLOR
    if os.environ.get("TERM") == "dumb":
        _HAS_COLOR = False
        return _HAS_COLOR
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
            _HAS_COLOR = True
        except Exception:
            _HAS_COLOR = False
    else:
        try:
            _HAS_COLOR = bool(sys.stdout.isatty())
        except Exception:
            _HAS_COLOR = True
    return _HAS_COLOR
def _truecolor_ok() -> bool:
    ct = os.environ.get("COLORTERM", "").lower()
    if "truecolor" in ct or "24bit" in ct:
        return True
    term = os.environ.get("TERM", "").lower()
    if sys.platform == "win32":
        return True
    return any(k in term for k in ("256color", "truecolor", "24bit", "xterm", "screen", "tmux", "rxvt", "linux", "cygwin"))
_GRAD_STOPS = ((0, 170, 60), (0, 255, 150), (255, 255, 255))
_BANNER_STOPS = ((0, 215, 75), (0, 255, 125), (255, 255, 255))
def _grad_mix(t: float, stops=None, ease: bool = True):
    stops = stops or _GRAD_STOPS
    t = max(0.0, min(1.0, t))
    if t >= 1.0:
        return stops[-1]
    if ease:
        t = t * t * (3 - 2 * t)
    segs = len(stops) - 1
    pos = min(t * segs, segs - 1e-9)
    i = int(pos)
    f = pos - i
    a, b = stops[i], stops[i + 1]
    return (int(a[0] + (b[0] - a[0]) * f),
            int(a[1] + (b[1] - a[1]) * f),
            int(a[2] + (b[2] - a[2]) * f))
def _rgb256(r, g, b):
    if r == g == b:
        if r < 8: return 16
        if r > 248: return 231
        return round((r - 8) / 247 * 23) + 232
    return 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)
def _grad(text, stops=None, ease: bool = True):
    if not _has_color() or not text:
        return str(text)
    text = str(text)
    n = len(text)
    use_tc = _truecolor_ok()
    out = []
    for i, ch in enumerate(text):
        t = i / max(1, n - 1)
        r, g, b = _grad_mix(t, stops=stops, ease=ease)
        if use_tc:
            out.append(f"\033[38;2;{r};{g};{b}m{ch}\033[0m")
        else:
            out.append(f"\033[38;5;{_rgb256(r,g,b)}m{ch}\033[0m")
    return "".join(out)
def _print(text):
    print(_grad(str(text)))
_BANNER = [
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠒⠒⠒⠒⠒⠒⠶⠶⠶⠶⢶⣤⣤⣤⣄⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⢉⣛⣻⣿⣶⣤⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⢀⣀⣠⣤⡤⠴⠶⠶⠶⠿⠛⠛⠛⠛⠛⠛⢛⣛⣻⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣠⡤⠶⠞⠛⠛⠛⠋⠉⢻⣧⣀⠀⠀⠀⠒⠤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⣀⡤⠖⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣿⣿⠿⠿⢷⣶⣦⣬⣙⢦⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠐⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣾⡿⠋⠀⠀⠀⠀⠀⠉⠙⠻⢿⣿⣷⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣾⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠻⢿⣿⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢿⣿⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⢿⣷⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠿⢿⣷⣶⣶⣤⣤⣤⣀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠉⠉⠙⠻⢿⣿⡻⢶⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢿⣦⠈⠙⢦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⣷⡀⠀⠙⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢷⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
]
_INTRO = ["───── Owner ─────",
          "       Dkh",
          "dkhang.pages.dev"]
def _cli_banner():
    try:
        tw = os.get_terminal_size().columns
    except Exception:
        tw = 80
    vis = [l.rstrip(" ⠀") for l in _BANNER]
    bw = max([len(l) for l in vis] + [0])
    iw = max([len(l) for l in _INTRO] + [0])
    gap = 4
    _bgrad = lambda s: _grad(s, stops=_BANNER_STOPS, ease=False)
    if tw >= bw + iw + gap and _INTRO:
        start = (len(vis) - len(_INTRO)) // 2
        for i, bl in enumerate(vis):
            left = _bgrad(bl)
            j = i - start
            if 0 <= j < len(_INTRO):
                pad = max(0, bw - len(bl) + gap)
                print(left + " " * pad + _grad(_INTRO[j]))
            else:
                print(left)
    else:
        for bl in vis:
            print(_bgrad(bl))
        print()
        for il in _INTRO:
            print(_grad(il.center(tw) if tw > len(il) else il))
    print()
_PREFIX = "[DkhObf]"
_CLI_TAG = "[DkhObf]"
_ASK_W = 17
def _inp(prompt):
    return input(_grad(_PREFIX) + " " + _grad(prompt)).strip()
def _cli_prompt(label: str) -> str:
    return input(_grad(_CLI_TAG) + "  " + _grad(label)).strip()
def _ask(label: str) -> str:
    return input(_grad(_CLI_TAG) + "  " + _grad(f"{label:<{_ASK_W}} : ")).strip()
def _ask_yn(label: str) -> str:
    return input(_grad(_CLI_TAG) + "  " + _grad(f"{label:<{_ASK_W}} : [Y/N] ")).strip().lower()
def _cli_read_username() -> str:
    while True:
        try:
            value = _ask("Username")
        except EOFError as e:
            raise DkhCliError("Username input aborted (EOF).") from e
        if value:
            return value
        print("Username cannot be empty.")
def _cli_read_filename():
    f = _ask("Enter File Name")
    if not f:
        raise DkhCliError("File name cannot be empty.")
    return f
def _resolve_input_path(filename: str) -> str:
    candidate = filename if os.path.isabs(filename) else os.path.join(os.getcwd(), filename)
    if not os.path.isfile(candidate):
        raise DkhCliError(f"File not found: {filename} (looked in {os.getcwd()})")
    if not candidate.endswith(".py"):
        raise DkhCliError(f"Input file must be a .py file: {filename}")
    return candidate
def _make_output_path(input_path: str) -> str:
    base_dir = os.path.dirname(input_path)
    base_name = os.path.basename(input_path)
    stem, ext = os.path.splitext(base_name)
    output_name = f"obf-{stem}{ext}"
    output_path = os.path.join(base_dir, output_name)
    if os.path.abspath(output_path) == os.path.abspath(input_path):
        output_path = os.path.join(base_dir, f"obf-{stem}-2{ext}")
    return output_path
def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    kb = num_bytes / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    return f"{mb:.2f} MB"
def _check_running_python_version(target_version: Tuple[int, int]) -> Optional[str]:
    major, minor = target_version
REQUIRED_PIP: List[str] = []
GUI_PIP: List[str] = ["customtkinter"]
def _pip_missing(packages: List[str]) -> List[str]:
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing
def _pip_install(packages: List[str]) -> bool:
    try:
        import subprocess as _sp
        _sp.run([sys.executable, "-m", "pip", "install", "--quiet"] + packages,
                check=True, timeout=300)
        return True
    except Exception:
        return False
def _ensure_deps_or_relaunch(packages: List[str]) -> None:
    missing = _pip_missing(packages)
    if not missing:
        return
    _print(f"Missing packages: {', '.join(missing)} — installing...")
    if os.environ.get("DKH_RELAUNCHED") == "1" or not _pip_install(missing):
        _print("Auto-install failed; continuing with limited mode.")
        return
    if _pip_missing(missing):
        _print("Auto-install failed; continuing with limited mode.")
        return
    _print("Installed — relaunching tool...")
    os.environ["DKH_RELAUNCHED"] = "1"
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv else None
    if argv0:
        os.execv(sys.executable, [sys.executable, argv0] + sys.argv[1:])
    if sys.version_info.major != major or sys.version_info.minor != minor:
        return (f"Warning: dkh.py is running under Python "
                f"{sys.version_info.major}.{sys.version_info.minor}, but DkhObfuscate "
                f"targets Python {major}.{minor}. The build may still succeed, but "
                f"is only tested against Python {major}.{minor}.")
    return None
class _LiveTimer:
    _INTERVAL = 0.1
    def __init__(self, t0: float):
        import threading
        self._t0 = t0
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
    def _run(self):
        import threading as _th
        while not self._stop_evt.wait(self._INTERVAL):
            try:
                sys.stdout.write("\r\033[K" + _grad(
                    f"[DkhObf] Obfuscating... Time: {time.time() - self._t0:.2f}s"))
                sys.stdout.flush()
            except Exception:
                return
    def start(self):
        self._thread.start()
    def stop(self):
        self._stop_evt.set()
        self._thread.join(timeout=1.0)
        try:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
        except Exception:
            pass
def run_cli():
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        pass
    _ensure_deps_or_relaunch(REQUIRED_PIP)
    _cli_banner()
    version_warning = _check_running_python_version((3, 12))
    if version_warning:
        _print(version_warning)
        print()
    try:
        username = _cli_read_username()
        filename = _cli_read_filename()
        input_path = _resolve_input_path(filename)
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                source = f.read()
        except OSError as e:
            raise DkhCliError(f"Could not read file: {e}")
        config = DragonConfig(
            enable_identifier_mangling=True,
            rename_module_level=True,
            enable_string_protection=True,
            enable_constant_protection=True,
            enable_structural_expansion=True,
            enable_control_flow=True,
            enable_vm=True,
            enable_integrity=True,
            enable_globals_storage=True,
            enable_user_payload_arch=True,
            enable_marshal_runtime=True,
            enable_runtime_guard=True,
            enable_memory_error_dispatch=True,
            enable_opaque_predicates=True,
            enable_builtins_indirection=False,
            identifier_style="hiragana",
            ast_depth=5,
            expansion_level="high",
            more_obfuscation=True,
            anti_combo=True,
            requests_protect=True,
            payload_compression_level=9,
            max_build_retries=3,
            username=username,
            target_python_version=(3, 12),
        )
        try:
            more_ans = _ask_yn("More Obfuscation")
            config.more_obfuscation = more_ans in ("y", "yes")
            config.script_mode = config.more_obfuscation
        except (EOFError, KeyboardInterrupt):
            pass
        config.enable_anti_decompli = config.more_obfuscation
        try:
            shield_ans = _ask_yn("Shield")
            config.anti_combo = shield_ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            config.anti_combo = True
        config.anti_debug = config.anti_combo
        config.anti_hook = config.anti_combo
        config.anti_dump = config.anti_combo
        try:
            req_ans = _ask_yn("Requests Protect")
            if req_ans:
                config.requests_protect = req_ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            pass
        config.enable_tamper_response = True
        print()
        t_overall = time.time()
        live_timer = _LiveTimer(t_overall)
        live_timer.start()
        report = None
        syntax_ok = False
        try:
            report = build(source, config, filename=input_path, validate=True,
                           run_validation=False)
        except DkhInputTooLargeError as e:
            raise DkhCliError(str(e))
        except SyntaxError as e:
            raise DkhCliError(f"Input file has a syntax error: {e}")
        except DkhSizeGovernorError as e:
            extra = ""
            if e.report is not None:
                r = e.report
                extra = (f" (final {_format_size(r.output_size_bytes)}, band "
                         f"{_format_size(r.output_floor)}.."
                         f"{_format_size(r.output_ceiling)}, grow rounds "
                         f"{r.boost_rounds}, escalations "
                         f"{r.size_governor_escalations}, diminishing="
                         f"{r.diminishing_returns_hit})")
            raise DkhCliError("Size governor could not land the output "
                                 f"inside the required DKH band{extra}. "
                                f"No output file written. {e}")
        except (DkhParseError, DkhTransformError,
                DkhCompileError, DkhRuntimeError) as e:
            raise DkhCliError(f"Transformation failed:\n{e}")
        except Exception:
            raise DkhCliError(f"Transformation failed:\n{traceback.format_exc()}")
        elapsed = time.time() - t_overall
        live_timer.stop()
        print(_grad(f"[DkhObf] Obfuscating... Time: {elapsed:.2f}s"))
        if report.validation and not report.validation.success:
            raise DkhCliError(
                f"Generated output failed validation.\n{report.validation.status_line()}"
            )
        syntax_ok = bool(report.validation and report.validation.compiled)
        output_path = _make_output_path(input_path)
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report.output_source or "")
        except OSError as e:
            raise DkhCliError(f"Could not write output file: {e}")
        input_size = report.input_size_bytes
        output_size = os.path.getsize(output_path)
        try:
            compile(ast.parse(report.output_source or ""),
                    filename=output_path, mode="exec")
            syntax_ok = True
        except Exception:
            syntax_ok = False
        total_time = time.time() - t_overall
        print()
        _print("──────────── Dkh Obfuscator ────────────")
        _print(f"  Username  : {username}")
        _print(f"  Input     : {os.path.basename(input_path)} ({_format_size(input_size)})")
        _print(f"  Output    : {os.path.basename(output_path)} ({_format_size(output_size)})")
        _print(f"  Location  : {output_path}")
        _print(f"  Python    : {sys.version_info.major}.{sys.version_info.minor}")
        _print(f"  Time      : {total_time:.1f}s")
        _print(f"  Status    : {'Success' if syntax_ok else 'Fail'}")
        print()
    except DkhCliError as e:
        try:
            if live_timer is not None:
                live_timer.stop()
        except (NameError, UnboundLocalError):
            pass
        print()
        _print("=" * 42)
        _print("        DZYDKH — BUILD FAILED")
        _print("=" * 42)
        print()
        print(f"Error: {e}")
        print()
        sys.exit(1)
    except KeyboardInterrupt:
        try:
            if live_timer is not None:
                live_timer.stop()
        except (NameError, UnboundLocalError):
            pass
        print("\nThank you for choosing Dkh Obfuscator.")
        sys.exit(1)
def launch_gui():
    try:
        import customtkinter as ctk
    except ImportError:
        _ensure_deps_or_relaunch(GUI_PIP)
        try:
            import customtkinter as ctk
        except ImportError:
            print("GUI mode requires the 'customtkinter' package, which is not installed.")
            print("Install it with:  pip install customtkinter")
            print("Falling back to CLI mode.\n")
            run_cli()
            return
    import threading
    from tkinter import filedialog
    COLOR_BG = "#120B1E"
    COLOR_PANEL = "#1C1230"
    COLOR_ACCENT = "#7B4FE0"
    COLOR_ACCENT_HOVER = "#9A6EF5"
    COLOR_EMBER = "#3E7BFA"
    COLOR_TEXT = "#E8E0FF"
    COLOR_MUTED = "#9C8FC4"
    COLOR_LOG_BG = "#0B0714"
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    root.title("DkhObfuscate — Python Code Protection")
    root.geometry("880x720")
    root.configure(fg_color=COLOR_BG)
    input_path_var = ctk.StringVar(value="")
    output_path_var = ctk.StringVar(value="")
    username_var = ctk.StringVar(value="Username")
    profile_var = ctk.StringVar(value="BALANCED")
    var_identifier = ctk.BooleanVar(value=True)
    var_string = ctk.BooleanVar(value=True)
    var_control_flow = ctk.BooleanVar(value=True)
    var_vm = ctk.BooleanVar(value=False)
    var_more_obfuscation = ctk.BooleanVar(value=False)
    var_anti_combo = ctk.BooleanVar(value=False)
    var_requests_protect = ctk.BooleanVar(value=True)
    def _update_profile_info(*_):
        pname = normalize_profile_name(profile_var.get())
        if pname == "CUSTOM":
            info_label.configure(text="Profile CUSTOM: toggle layers manually.")
            return
        probe_cfg = DragonConfig().apply_profile(pname)
        try:
            in_bytes = os.path.getsize(input_path_var.get()) \
                if input_path_var.get() and os.path.isfile(input_path_var.get()) else 0
        except OSError:
            in_bytes = 0
        try:
            band_txt = _dkh_band_label(in_bytes) if in_bytes >= 1 else "n/a (select input)"
        except ValueError:
            band_txt = "out of Dkh range (needs input below 2 MB)"
        est = ("VM " + ("on" if probe_cfg.enable_vm else "off")
               + " · expansion " + str(probe_cfg.expansion_level)
               + " · depth " + str(probe_cfg.ast_depth))
        info_label.configure(
            text=f"Profile {pname}: {est} · expected output band: {band_txt}")
    header = ctk.CTkFrame(root, fg_color=COLOR_PANEL, corner_radius=0)
    header.pack(fill="x", side="top")
    ctk.CTkLabel(header, text="K Y U R A", font=("Georgia", 22, "bold"),
                 text_color=COLOR_ACCENT).pack(side="left", padx=20, pady=14)
    ctk.CTkLabel(header, text="Python Code Protection & Obfuscation (targets Python 3.12)",
                 font=("Georgia", 12), text_color=COLOR_MUTED).pack(side="left", pady=14)
    body = ctk.CTkFrame(root, fg_color=COLOR_BG)
    body.pack(fill="both", expand=True, padx=16, pady=16)
    file_panel = ctk.CTkFrame(body, fg_color=COLOR_PANEL, corner_radius=10)
    file_panel.pack(fill="x", pady=(0, 12))
    ctk.CTkLabel(file_panel, text="Source & Output", font=("Georgia", 14, "bold"),
                 text_color=COLOR_TEXT).pack(anchor="w", padx=14, pady=(10, 4))
    def text_row(parent, label, var):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(row, text=label, width=100, anchor="w", text_color=COLOR_MUTED).pack(side="left")
        ctk.CTkEntry(row, textvariable=var, fg_color=COLOR_BG, text_color=COLOR_TEXT,
                     border_color=COLOR_ACCENT).pack(side="left", fill="x", expand=True, padx=8)
    def file_row(parent, label, var, command):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(row, text=label, width=100, anchor="w", text_color=COLOR_MUTED).pack(side="left")
        ctk.CTkEntry(row, textvariable=var, fg_color=COLOR_BG, text_color=COLOR_TEXT,
                     border_color=COLOR_ACCENT).pack(side="left", fill="x", expand=True, padx=8)
        ctk.CTkButton(row, text="Browse", width=80, fg_color=COLOR_EMBER,
                      hover_color=COLOR_ACCENT_HOVER, command=command).pack(side="left")
    def pick_input():
        path = filedialog.askopenfilename(filetypes=[("Python files", "*.py")])
        if path:
            input_path_var.set(path)
            if not output_path_var.get():
                output_path_var.set(_make_output_path(path))
            _update_profile_info()
    def pick_output():
        path = filedialog.asksaveasfilename(defaultextension=".py", filetypes=[("Python files", "*.py")])
        if path:
            output_path_var.set(path)
    text_row(file_panel, "Username:", username_var)
    file_row(file_panel, "Input (.py):", input_path_var, pick_input)
    file_row(file_panel, "Output (.py):", output_path_var, pick_output)
    profile_row = ctk.CTkFrame(file_panel, fg_color="transparent")
    profile_row.pack(fill="x", padx=14, pady=4)
    ctk.CTkLabel(profile_row, text="Profile:", width=100, anchor="w",
                 text_color=COLOR_MUTED).pack(side="left")
    ctk.CTkOptionMenu(profile_row, values=["SAFE", "BALANCED", "HARD", "MAX", "CUSTOM"],
                      variable=profile_var, fg_color=COLOR_BG,
                      button_color=COLOR_ACCENT,
                      button_hover_color=COLOR_ACCENT_HOVER,
                      command=_update_profile_info).pack(side="left", padx=8)
    info_label = ctk.CTkLabel(file_panel, text="", anchor="w",
                              text_color=COLOR_MUTED, justify="left")
    info_label.pack(fill="x", padx=14, pady=(0, 8))
    _update_profile_info()
    modules_panel = ctk.CTkFrame(body, fg_color=COLOR_PANEL, corner_radius=10)
    modules_panel.pack(fill="x", pady=(0, 12))
    ctk.CTkLabel(modules_panel, text="Protection Layers", font=("Georgia", 14, "bold"),
                 text_color=COLOR_TEXT).pack(anchor="w", padx=14, pady=(10, 4))
    toggles = ctk.CTkFrame(modules_panel, fg_color="transparent")
    toggles.pack(fill="x", padx=14, pady=(0, 12))
    def toggle(parent, label, var):
        ctk.CTkCheckBox(parent, text=label, variable=var, text_color=COLOR_TEXT,
                        fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER).pack(anchor="w", pady=3)
    toggle(toggles, "RuneMangler — Identifier Transformation (Hiragana/CJK)", var_identifier)
    toggle(toggles, "SerpentCipher/EmberCipher — String / Data Protection", var_string)
    toggle(toggles, "WyrmFlow — Control-Flow Transformation", var_control_flow)
    toggle(toggles, "Dkh VM v2 — Selective Virtualization (permuted-int)", var_vm)
    toggle(toggles, "More Obfuscation (includes Anti-Decompli, Layer 3)", var_more_obfuscation)
    toggle(toggles, "Shield (anti-debug + anti-hook + anti-dump)", var_anti_combo)
    toggle(toggles, "Request Protect (+ Anti-Tamper Response)", var_requests_protect)
    action_row = ctk.CTkFrame(body, fg_color="transparent")
    action_row.pack(fill="x", pady=(0, 12))
    build_button = ctk.CTkButton(action_row, text="Forge (Build)", font=("Georgia", 14, "bold"),
                                  fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, height=40)
    build_button.pack(side="left")
    progress = ctk.CTkProgressBar(action_row, fg_color=COLOR_PANEL, progress_color=COLOR_EMBER)
    progress.pack(side="left", fill="x", expand=True, padx=(14, 0))
    progress.set(0)
    log_panel = ctk.CTkFrame(body, fg_color=COLOR_PANEL, corner_radius=10)
    log_panel.pack(fill="both", expand=True)
    ctk.CTkLabel(log_panel, text="Forge Log", font=("Georgia", 14, "bold"),
                 text_color=COLOR_TEXT).pack(anchor="w", padx=14, pady=(10, 4))
    log_box = ctk.CTkTextbox(log_panel, fg_color=COLOR_LOG_BG, text_color=COLOR_TEXT,
                              font=("Consolas", 11), corner_radius=8)
    log_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
    log_box.configure(state="disabled")
    def log(msg):
        log_box.configure(state="normal")
        log_box.insert("end", msg + "\n")
        log_box.configure(state="disabled")
        log_box.see("end")
    def safe_log(msg):
        root.after(0, lambda: log(msg))
    def safe_progress(v):
        root.after(0, lambda: progress.set(v))
    def on_build():
        ipath = input_path_var.get().strip()
        opath = output_path_var.get().strip()
        if not ipath or not os.path.isfile(ipath):
            log("[ERROR] Please select a valid input .py file.")
            return
        if not opath:
            log("[ERROR] Please select an output location.")
            return
        build_button.configure(state="disabled")
        progress.set(0.1)
        threading.Thread(target=do_build, args=(ipath, opath), daemon=True).start()
    def do_build(ipath, opath):
        try:
            with open(ipath, "r", encoding="utf-8") as f:
                source = f.read()
            safe_log(f"[BUILD] Reading {ipath}")
            safe_progress(0.3)
            cfg = DragonConfig(
                enable_identifier_mangling=var_identifier.get(),
                enable_string_protection=var_string.get(),
                enable_control_flow=var_control_flow.get(),
                enable_vm=var_vm.get(),
                more_obfuscation=var_more_obfuscation.get(),
                enable_anti_decompli=var_more_obfuscation.get(),
                anti_combo=var_anti_combo.get(),
                anti_debug=var_anti_combo.get(),
                anti_hook=var_anti_combo.get(),
                anti_dump=var_anti_combo.get(),
                requests_protect=var_requests_protect.get(),
                enable_tamper_response=True,
                identifier_style="hiragana",
                expansion_level="high",
                ast_depth=5,
                payload_compression_level=9,
                username=username_var.get().strip() or "Username",
                target_python_version=(3, 12),
            )
            if normalize_profile_name(profile_var.get()) != "CUSTOM":
                cfg.apply_profile(profile_var.get())
            report = build(source, cfg, filename=ipath, validate=True, run_validation=False)
            for stage in report.stages_run:
                safe_log(f"  -> {stage}")
            safe_progress(0.7)
            if report.validation and not report.validation.success:
                safe_log(report.validation.status_line())
                safe_progress(0)
                return
            if report.output_band_status != "in-band":
                safe_log(f"[WARN] Output {_format_size(report.output_size_bytes)} is "
                         f"{report.output_band_status}; required band "
                         f"[{_format_size(report.output_floor)}.."
                         f"{_format_size(report.output_ceiling)}] — writing anyway; "
                         f"protection stays fully functional.")
            with open(opath, "w", encoding="utf-8") as f:
                f.write(report.output_source or "")
            safe_progress(0.85)
            safe_log("[VALIDATE] Executing output to confirm it runs...")
            exec_ok, exec_msg = execute_output_for_validation(report.output_source or "", timeout=60.0)
            if exec_ok:
                safe_log(f"[SUCCESS] Output written to {opath}")
                safe_log(f"  Size: {_format_size(report.input_size_bytes)} -> "
                         f"{_format_size(os.path.getsize(opath))} "
                         f"({report.expansion_ratio:.2f}x, budget "
                         f"{_format_size(report.hard_output_budget)})")
                safe_progress(1.0)
            else:
                safe_log(f"[FAIL] Output was written but did not execute correctly: {exec_msg}")
                safe_progress(0)
        except Exception:
            safe_log(f"[ERROR]\n{traceback.format_exc()}")
            safe_progress(0)
        finally:
            root.after(0, lambda: build_button.configure(state="normal"))
    build_button.configure(command=on_build)
    root.mainloop()
def _docstring_spans(tree: ast.AST) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    def _grab(node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                spans.append((first.lineno,
                              first.end_lineno or first.lineno))
    _grab(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            _grab(node)
    return spans
def build_release_source(dev_source: str) -> str:
    import io as _mio
    import tokenize as _mtk
    import re as _mre
    tree = ast.parse(dev_source)
    spans = _docstring_spans(tree)
    in_doc = [False] * (len(dev_source.splitlines()) + 2)
    for start, end in spans:
        for ln in range(start, end + 1):
            if ln < len(in_doc):
                in_doc[ln] = True
    line_toks: Dict[int, List[int]] = {}
    try:
        toks = _mtk.generate_tokens(_mio.StringIO(dev_source).readline)
        for tok in toks:
            line_toks.setdefault(tok.start[0], []).append(tok.type)
    except (_mtk.TokenError, SyntaxError, IndentationError):
        pass
    _KEEP_TOKS = {_mtk.STRING, _mtk.NL, _mtk.NEWLINE, _mtk.COMMENT,
                  _mtk.INDENT, _mtk.DEDENT, _mtk.ENCODING, _mtk.ENDMARKER}
    _CODE_TOKS = {_mtk.NAME, _mtk.OP, _mtk.NUMBER}
    out: List[str] = []
    for i, raw in enumerate(dev_source.splitlines(keepends=True), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if i == 1 and stripped.startswith("#!"):
            out.append(raw)
            continue
        if i == 2 and _mre.search(r"coding[:=]", stripped):
            out.append(raw)
            continue
        toks = line_toks.get(i, [])
        if toks and all(t in _KEEP_TOKS for t in toks):
            if any(t == _mtk.COMMENT for t in toks) and not in_doc[i]:
                continue
        if in_doc[i] and not any(t in _CODE_TOKS for t in toks):
            continue
        out.append(raw)
    text = "".join(out)
    return text if text.endswith("\n") else text + "\n"
def release_minification_stats(dev_source: str,
                               rel_source: str) -> Dict[str, int]:
    return {"source_lines": len(dev_source.splitlines()),
            "release_lines": len(rel_source.splitlines()),
            "lines_removed": (len(dev_source.splitlines())
                              - len(rel_source.splitlines()))}
def main():
    run_cli()
if __name__ == "__main__":
    main()
