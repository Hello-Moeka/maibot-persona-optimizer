"""插件配置模型。"""

from __future__ import annotations

from typing import ClassVar, Literal

from maibot_sdk import Field, PluginConfigBase


CONFIG_VERSION = "1.0.0"


class PluginSection(PluginConfigBase):
    """插件基础设置。"""

    __ui_label__: ClassVar[str] = "插件"
    __ui_icon__: ClassVar[str] = "sparkles"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(default=True, description="是否启用自动人设优化。")
    config_version: str = Field(
        default=CONFIG_VERSION,
        description="配置结构版本。",
        json_schema_extra={"hidden": True, "disabled": True},
    )


class OptimizationSection(PluginConfigBase):
    """优化流程设置。"""

    __ui_label__: ClassVar[str] = "优化流程"
    __ui_icon__: ClassVar[str] = "wand-sparkles"
    __ui_order__: ClassVar[int] = 1

    requirement: str = Field(
        default="",
        description="人设优化要求；也可通过 QQ 指令设置。留空时自动任务不会执行。",
        json_schema_extra={
            "x-widget": "textarea",
            "label": "优化要求",
            "placeholder": "例如：保持温柔底色，但减少模板化安慰，多结合聊天上下文自然回应。",
        },
    )
    interval_minutes: int = Field(
        default=60,
        ge=5,
        le=1440,
        description="自动优化间隔，默认每 60 分钟执行一次。",
    )
    minimum_messages: int = Field(
        default=8,
        ge=1,
        le=1000,
        description="时间窗内少于该消息数时跳过优化，避免样本过少。",
    )
    max_total_messages: int = Field(
        default=500,
        ge=10,
        le=5000,
        description="每轮最多读取的消息总数。",
    )
    max_messages_per_stream: int = Field(
        default=100,
        ge=5,
        le=1000,
        description="每个聊天流最多保留的消息数。",
    )
    max_streams: int = Field(
        default=20,
        ge=1,
        le=200,
        description="每轮最多分析的聊天流数。",
    )
    max_total_chars: int = Field(
        default=60000,
        ge=2000,
        le=500000,
        description="送入概括阶段的聊天记录总字符上限。",
    )
    summary_chunk_chars: int = Field(
        default=12000,
        ge=1000,
        le=100000,
        description="聊天记录分块概括时的单块字符上限。",
    )
    max_personality_chars: int = Field(
        default=12000,
        ge=200,
        le=50000,
        description="允许生成的人格设定最大字符数。",
    )
    max_reply_style_chars: int = Field(
        default=8000,
        ge=100,
        le=30000,
        description="允许生成的表达风格最大字符数。",
    )


class ChatSourceSection(PluginConfigBase):
    """聊天记录来源设置。"""

    __ui_label__: ClassVar[str] = "聊天来源"
    __ui_icon__: ClassVar[str] = "messages-square"
    __ui_order__: ClassVar[int] = 2

    platforms: list[str] = Field(
        default_factory=lambda: ["qq"],
        description="纳入分析的平台列表，QQ 使用 qq。",
    )
    include_group_chats: bool = Field(default=True, description="是否分析群聊。")
    include_private_chats: bool = Field(
        default=False,
        description="是否分析私聊。私聊可能包含敏感信息，默认关闭。",
    )
    excluded_stream_ids: list[str] = Field(
        default_factory=list,
        description="不参与分析的聊天流 ID。",
    )
    exclude_commands: bool = Field(default=True, description="是否排除命令消息。")


class ModelSection(PluginConfigBase):
    """模型路由设置。"""

    __ui_label__: ClassVar[str] = "模型"
    __ui_icon__: ClassVar[str] = "brain-circuit"
    __ui_order__: ClassVar[int] = 3

    mode: Literal["planner", "replyer", "maibot_task", "openai_compatible"] = Field(
        default="planner",
        description="使用 planner、replyer、其他 MaiBot 模型任务或自定义 OpenAI 兼容模型。",
    )
    maibot_task_name: str = Field(
        default="utils",
        description="mode=maibot_task 时使用的 MaiBot 模型任务名。",
    )
    temperature: float = Field(default=0.3, ge=0, le=2, description="模型温度。")
    summary_max_tokens: int = Field(default=1800, ge=128, le=32000, description="单次聊天概括最大输出。")
    direction_max_tokens: int = Field(default=2200, ge=128, le=32000, description="优化方向最大输出。")
    persona_max_tokens: int = Field(default=4000, ge=256, le=64000, description="优化人设最大输出。")
    custom_endpoint: str = Field(
        default="",
        description="OpenAI 兼容接口地址，可填 base URL、/v1 或完整 /chat/completions 地址。",
        json_schema_extra={"placeholder": "https://api.example.com/v1"},
    )
    custom_api_key: str = Field(
        default="",
        description="自定义接口 API Key。为安全起见不能通过 QQ 指令设置。",
        json_schema_extra={"x-widget": "password", "sensitive": True},
    )
    custom_model_name: str = Field(
        default="",
        description="自定义 OpenAI 兼容接口的模型标识。",
    )
    custom_timeout_seconds: int = Field(
        default=180,
        ge=10,
        le=900,
        description="自定义接口请求超时秒数。",
    )


class SecuritySection(PluginConfigBase):
    """QQ 指令权限设置。"""

    __ui_label__: ClassVar[str] = "权限"
    __ui_icon__: ClassVar[str] = "shield-check"
    __ui_order__: ClassVar[int] = 4

    administrators: list[str] = Field(
        default_factory=list,
        description="可控制插件的账号，推荐格式 qq:123456；也兼容只填 QQ 号。",
    )
    inherit_plugin_management_permissions: bool = Field(
        default=True,
        description="同时允许 bot_config.toml 中 plugin.permission 的管理员。",
    )
    allow_public_help: bool = Field(default=True, description="是否允许非管理员查看指令帮助。")


class StorageSection(PluginConfigBase):
    """结果保存设置。"""

    __ui_label__: ClassVar[str] = "保存"
    __ui_icon__: ClassVar[str] = "archive"
    __ui_order__: ClassVar[int] = 5

    history_limit: int = Field(default=50, ge=1, le=1000, description="最多保留的历史优化版本数。")
    command_output_chars: int = Field(
        default=3500,
        ge=500,
        le=12000,
        description="QQ 查看单个优化版本时的最大输出字符数。",
    )


class PersonaOptimizerConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    optimization: OptimizationSection = Field(default_factory=OptimizationSection)
    chat_source: ChatSourceSection = Field(default_factory=ChatSourceSection)
    model: ModelSection = Field(default_factory=ModelSection)
    security: SecuritySection = Field(default_factory=SecuritySection)
    storage: StorageSection = Field(default_factory=StorageSection)

