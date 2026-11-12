"""
Display-layer Chinese mappings for knowledge-graph visualization.

Does not modify GraphML, LightRAG storage, or entity IDs.
"""

from __future__ import annotations

import re

# Common industrial pump entities (English key -> Chinese label).
# Keys are matched case-insensitively against entity display names / IDs.
ENTITY_ZH: dict[str, str] = {
    "water pump": "水泵",
    "pump": "泵",
    "centrifugal pump": "离心泵",
    "mechanical seal": "机械密封",
    "seal": "密封",
    "impeller": "叶轮",
    "bearing": "轴承",
    "shaft": "轴",
    "pump casing": "泵壳",
    "casing": "泵壳",
    "volute": "蜗壳",
    "gland": "填料压盖",
    "packing": "填料",
    "coupling": "联轴器",
    "motor": "电机",
    "baseplate": "底座",
    "base plate": "底座",
    "suction": "吸入",
    "discharge": "排出",
    "cavitation": "气蚀",
    "lubrication": "润滑",
    "oil": "润滑油",
    "grease": "润滑脂",
    "temperature": "温度",
    "vibration": "振动",
    "alignment": "对中",
    "clearance": "间隙",
    "dial indicator": "千分表",
    "dial indicator method": "千分表法",
    "bearing housing": "轴承箱",
    "wear ring": "耐磨环",
    "o-ring": "O 型圈",
    "gasket": "垫片",
    "valve": "阀门",
    "pipe": "管道",
    "piping": "管路",
    "flush": "冲洗",
    "cooling": "冷却",
    "startup": "启动",
    "start-up": "启动",
    "shutdown": "停机",
    "maintenance": "维护",
    "installation": "安装",
    "troubleshooting": "故障排查",
}

# LightRAG entity_type values -> Chinese legend labels.
TYPE_ZH: dict[str, str] = {
    "artifact": "设备",
    "concept": "概念",
    "content": "内容",
    "location": "位置",
    "naturalobject": "自然对象",
    "organization": "组织",
    "method": "方法",
    "event": "事件",
    "person": "人员",
    "data": "数据",
    "other": "其他",
    "component": "部件",
    "symptom": "症状",
    "process": "过程",
    "procedure": "流程",
    "measurement": "参数",
    "material": "材料",
    "document": "文档",
    "unknown": "未知",
}


def map_entity_zh(name: str) -> str | None:
    """Return Chinese label for an English entity name, or None if unmapped."""
    key = (name or "").strip().casefold()
    if not key:
        return None
    return ENTITY_ZH.get(key)


def map_type_zh(entity_type: str) -> str:
    """Return Chinese type label; fall back to original when unmapped.

    Inferred display categories are marked with a ``(推断)`` suffix so a reader
    can never mistake them for the extractor's own ``entity_type``.
    """

    raw = (entity_type or "").strip()
    if not raw:
        return "未知"
    inferred = is_inferred_type(raw)
    bare = strip_inferred_prefix(raw)
    mapped = TYPE_ZH.get(bare.casefold(), bare)
    return f"{mapped}(推断)" if inferred else mapped


def bilingual_entity_label(english_name: str, *, multiline: bool = True) -> str:
    """Build bilingual display text: Chinese + (English) when mapped."""
    en = (english_name or "").strip()
    if not en:
        return ""
    zh = map_entity_zh(en)
    if not zh:
        return en
    if multiline:
        return f"{zh}\n({en})"
    return f"{zh} ({en})"


# ---------------------------------------------------------------------------
# Inferred display categories
# ---------------------------------------------------------------------------
# LightRAG writes only ``entity_name``, ``source_id``, ``content`` and
# ``file_path`` into ``entities_vdb``; the extractor's ``entity_type`` is never
# persisted (``full_entities`` keeps names only).  Graph payloads therefore carry
# no type at all, and every node rendered as "未知".
#
# The helper below infers a *display* category from the entity name so the
# legend is readable.  This is a presentation aid only: it is never written back
# to storage, never used for retrieval, and must not be presented as an
# extraction result.
INFERRED_TYPE_PREFIX = "inferred:"

# Storage keys (child/parent chunk IDs) are identifiers, not entities.
_INTERNAL_CHUNK_ID = re.compile(r"(?:cchunk|pchunk)-[a-z0-9-]+")

# Order matters: the first matching category wins, so more specific categories
# are listed before the generic component/measurement buckets.
_INFERRED_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "symptom",
        (
            "leak", "leakage", "vibration", "noise", "cavitation", "overheat",
            "failure", "fault", "damage", "wear", "corrosion", "blockage",
            "hazard", "danger", "risk", "injury",
            "泄漏", "振动", "噪声", "气蚀", "过热", "故障", "损坏", "磨损",
            "腐蚀", "堵塞", "失效", "危险", "风险", "伤害",
        ),
    ),
    (
        "procedure",
        (
            "installation", "installing", "assembly", "disassembly", "removal",
            "maintenance", "repair", "startup", "start-up", "shutdown", "flush",
            "alignment", "commissioning", "procedure", "inspection",
            "安装", "拆卸", "维护", "检修", "启动", "停机", "冲洗", "对中",
            "调试", "检查", "步骤", "程序",
        ),
    ),
    (
        "component",
        (
            "seal", "impeller", "bearing", "shaft", "casing", "volute", "gland",
            "packing", "coupling", "motor", "baseplate", "base plate", "gasket",
            "o-ring", "ring", "valve", "pipe", "piping", "bolt", "nut", "washer",
            "key", "bushing", "sleeve", "plate", "cover", "flange", "guard",
            "plug", "fastener", "locknut", "foundation", "indicator", "filter",
            "strainer", "impeller clearance",
            "机械密封", "密封", "叶轮", "轴承", "泵轴", "轴", "泵壳", "蜗壳",
            "填料", "压盖", "联轴器", "电机", "底座", "基础", "垫片", "垫圈",
            "O 型圈", "阀门", "截断装置", "管道", "管路", "螺栓", "螺母",
            "键", "衬套", "法兰", "防护罩", "堵头", "紧固件", "千分表", "过滤器",
        ),
    ),
    (
        "measurement",
        (
            "temperature", "pressure", "flow", "speed", "rpm", "torque",
            "viscosity", "npsh", "clearance", "dimension", "weight", "capacity",
            "head", "limit", "rate", "ratio",
            "温度", "压力", "流量", "转速", "扭矩", "粘度", "间隙", "尺寸",
            "重量", "扬程", "上限", "下限", "限值", "参数",
        ),
    ),
    (
        "material",
        (
            "material", "stainless", "steel", "cast iron", "bronze", "alloy",
            "rubber", "ptfe", "ceramic", "carbon",
            "材料", "不锈钢", "钢", "铸铁", "青铜", "合金", "橡胶", "陶瓷",
        ),
    ),
    (
        "organization",
        (
            "desmi", "summit", "company", "manufacturer", "supplier", "vendor",
            "customer", "department", "warranty",
            "公司", "制造商", "供应商", "客户", "部门", "质保",
        ),
    ),
    (
        "document",
        (
            "manual", "catalog", "catalogue", "datasheet", "drawing", "figure",
            "table", "chart", "section", "chapter", "page", "nameplate",
            "手册", "目录", "样本", "图纸", "图", "表", "章节", "页", "铭牌",
        ),
    ),
)


def _contains_term(key: str, term: str) -> bool:
    """Match a term as a whole word for English, as a substring for Chinese.

    Plain ``in`` matching mis-typed ``Bearing Temperature`` as a component
    because it contains "bearing"; the measurement noun is the head word.
    """

    if not term:
        return False
    if any("\u3400" <= char <= "\u9fff" for char in term):
        return term in key
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", key) is not None


def infer_entity_type(name: str) -> str | None:
    """Infer a display category from an entity name.

    Chinese noun phrases are head-final, so a term appearing at the end of the
    name wins over one appearing earlier: ``Bearing Temperature`` is a
    measurement (head: temperature), not a component (modifier: bearing).

    Returns a prefixed marker (``inferred:<category>``) so callers and the UI can
    tell an inferred display category from a real extraction ``entity_type``.
    Returns ``None`` when no rule matches.
    """

    key = (name or "").strip().casefold()
    if not key:
        return None
    # Internal chunk IDs embed the source section (e.g. "...-故障诊断-38fb20d9"),
    # so matching their text would classify storage keys by their page heading.
    if _INTERNAL_CHUNK_ID.search(key):
        return None
    matches: list[tuple[int, str]] = []
    for category, terms in _INFERRED_TYPE_RULES:
        for term in terms:
            if not _contains_term(key, term):
                continue
            matches.append((key.rfind(term) + len(term), f"{INFERRED_TYPE_PREFIX}{category}"))
    if not matches:
        return None
    # Right-most (head) match wins; ties keep rule order for determinism.
    best_position = max(position for position, _ in matches)
    return next(
        category for position, category in matches if position == best_position
    )


def is_inferred_type(entity_type: str) -> bool:
    """Return whether a type value came from :func:`infer_entity_type`."""

    return (entity_type or "").startswith(INFERRED_TYPE_PREFIX)


def strip_inferred_prefix(entity_type: str) -> str:
    """Return the bare category for legend/colour lookups."""

    raw = (entity_type or "").strip()
    if is_inferred_type(raw):
        return raw[len(INFERRED_TYPE_PREFIX) :]
    return raw
