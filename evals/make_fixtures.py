"""Build the two eval projects into whatever HARNOQ_SQLITE_PATH points at.

NEVER run this against harnoq.db — it writes. Point the env var at a temp file.

Neither fixture is a real user project on purpose: tuning the prompt against one
real project risks overfitting it to that domain. E1 and E2
differ in domain AND in shape (E2 has no user interface at all), so a rule that
only works for GUI-shaped projects shows up as a failure on E2.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.core.config import Config  # noqa: E402
from app.db.base import database_proxy
from app.db.schema import create_schema  # noqa: E402
from app.db.session import create_database
from app.services import (
    feature_service,
    project_constitution_service,
    project_service,
    source_requirement_service,
)

if os.path.basename(os.environ.get("HARNOQ_SQLITE_PATH", "harnoq.db")) == "harnoq.db":
    raise SystemExit(
        "refusing to write the default database — set HARNOQ_SQLITE_PATH to a temp file"
    )

_db = create_database(Config())
database_proxy.initialize(_db)
_db.connect(reuse_if_open=True)
create_schema(_db)


def build(project, requirement, constitution, features, deps):
    p = project_service.create(project)
    sr = source_requirement_service.create(p.id, requirement)
    source_requirement_service.confirm(sr.id)
    project_constitution_service.update(p.id, constitution)
    project_constitution_service.confirm(p.id)
    made = {}
    for spec in features:
        made[spec["name"]] = feature_service.create(p.id, spec).feature_number
    by_number = {f.feature_number: f for f in feature_service.list_by_project(p.id)}
    for name, upstream in deps.items():
        feature_service.update(by_number[made[name]].id, {
            "depends_on_feature_numbers": [made[u] for u in upstream],
        })
    print(f"{p.project_number}  {p.name}  ({len(features)} features)")
    return p.project_number


def _desc(owns, contract, data, excludes):
    return (f"归属: {owns}\n契约: {contract}\n关键数据: {data}\n不做: {excludes}")


build(
    {"name": "排课系统", "description": "面向一所中学的学期排课与调课工具,教务处使用。"},
    {
        "title": "排课与调课",
        "source_type": "initial_document",
        "raw_content": """
教务处每学期要给全校 40 个班、约 120 名教师排一学期的课表。

- 现在用表格软件排,一个人排两周,冲突靠肉眼看,每学期都会漏掉几处。
- 硬约束:同一教师同一时段只能一节课;同一班级同一时段只能一节课;专用教室
  同一时段只能一个班使用。
- 软约束:体育课尽量不排在下午最后一节;同一学科一天不超过两节;教师尽量集中
  在半天,不要一天跑三个时段各一节。
- 排好之后经常要调课:某教师请假,需要在不破坏硬约束的前提下换课,并且要看到
  这次调动影响了哪些班级和哪些教师。
- 排课结果要能按班级、按教师两种视角查看和打印。
- 学期中途会新增或撤销课程,需要局部重排而不是全部推倒。
- 规模:一学期约 40 个班 x 每周 35 节 = 1400 个课时槽位,希望一次自动排课在
  一分钟内给出结果。
- 教务处只有一台普通办公电脑,不上云,数据不能出校。
- 排课失败时要能说清楚是哪条硬约束冲突了,而不是只说"排不出来"。
""".strip(),
    },
    {
        "system_nature": "单机离线的约束求解型教务工具:核心是带硬/软约束的排课引擎,"
                         "外围是课表的录入、查看与局部调整。承重结构是课程安排的唯一"
                         "事实来源、约束校验、以及一次排课的可重复性。",
        "fixed_stack": "Python;单机 Windows 桌面运行;本地文件存储;不联网。"
                       "界面形态与数据存放形态在设计阶段点名后回收至此。",
        "quality_bar": "硬约束零违反是功能正确性的一部分,必须有自动化测试覆盖;"
                       "每个模块可独立测试。待确认:覆盖率门槛。",
        "ui_conventions": "列表页优先,添加/编辑在弹出框中完成,删除需确认。",
        "nfr_baseline": "一次全量自动排课在 1 分钟内返回;数据规模约 1400 个课时槽位。"
                        "调课的影响范围计算需即时反馈(待确认具体秒数)。",
        "boundaries": "always:本机运行、结果可重复。ask-first:引入任何第三方求解器或"
                      "新框架。never:数据出校、联网上传、自动覆盖已发布的课表。",
    },
    [
        {"name": "系统基础框架", "orientation": "foundation", "subdomain_type": "supporting",
         "foundation_kind": "architecture", "is_spike": False,
         "description": _desc("应用骨架、模块接缝、本地数据存放与读写边界、错误与诊断约定、测试脚手架",
                              "向上提供模块注册、数据读写、错误上报三类契约;不依赖任何业务模块",
                              "数据存放位置与版本、模块注册表",
                              "不含任何排课规则、约束判定或界面")},
        {"name": "界面基础框架", "orientation": "foundation", "subdomain_type": "supporting",
         "foundation_kind": "ui", "is_spike": False,
         "description": _desc("导航、布局、列表/详情/弹框模式、表单与空/载入/错误状态的统一约定",
                              "向各页面提供共享组件与交互规范", "无持久状态",
                              "不含具体业务页面的字段与规则")},
        {"name": "排课引擎", "orientation": "foundation", "subdomain_type": "core",
         "is_spike": True,
         "description": _desc("硬约束判定、软约束评分、一次排课的求解与可重复性",
                              "给定课程、教师、班级、教室与约束集合,返回一份安排或一份冲突说明",
                              "求解过程中的安排状态;不拥有持久化课表",
                              "不负责课表的存储、展示与调课流程编排")},
        {"name": "基础数据维护", "orientation": "vertical", "subdomain_type": "supporting",
         "is_spike": False,
         "description": _desc("班级、教师、课程、教室及其可用时段的录入与维护",
                              "向排课与调课提供当前有效的基础数据集合",
                              "班级/教师/课程/教室及可用性,是这些实体的唯一事实来源",
                              "不判定约束、不产生课表")},
        {"name": "排课与调课", "orientation": "vertical", "subdomain_type": "core",
         "is_spike": False,
         "description": _desc("发起一次排课、查看结果、局部调整、以及调动的影响范围计算",
                              "向查看与打印提供已发布课表",
                              "已发布课表及其版本,是课表的唯一事实来源",
                              "不实现求解算法本身,不维护基础数据")},
        {"name": "课表查看与打印", "orientation": "vertical", "subdomain_type": "supporting",
         "is_spike": False,
         "description": _desc("按班级与按教师两种视角呈现课表,并支持打印",
                              "只读消费已发布课表", "无",
                              "不修改课表、不判定约束")},
    ],
    {
        "界面基础框架": ["系统基础框架"],
        "排课引擎": ["系统基础框架"],
        "基础数据维护": ["系统基础框架", "界面基础框架"],
        "排课与调课": ["系统基础框架", "界面基础框架", "排课引擎", "基础数据维护"],
        "课表查看与打印": ["系统基础框架", "界面基础框架", "排课与调课"],
    },
)

build(
    {"name": "日志聚合与告警", "description": "无人值守的后台服务,收集若干台机器的运行日志并在异常时通知值班人。"},
    {
        "title": "日志收集与告警",
        "source_type": "initial_document",
        "raw_content": """
我们有十几台机器在跑各自的任务,出问题时要翻每台机器的日志,经常是第二天才发现。

- 每台机器把自己的日志行发过来,汇总到一处保存,能按时间和来源机器查。
- 日志行会乱序到达,也会因为重发而重复;同一行不能在结果里出现两次。
- 某台机器断网一段时间后会把积压的日志一次性补发,补发量可能很大,不能把
  接收端压垮,也不能因此丢掉正在实时到达的日志。
- 值班人配置若干条规则,例如"某个来源在五分钟内出现十次以上错误级别"就通知。
- 通知目前只需要发到一个内部的消息接口,发送失败要重试,并且同一次异常不要
  重复轰炸。
- 没有界面,全部通过配置文件和命令行操作;查询用命令行给出。
- 日志保留三十天,过期自动清理,清理不能影响正在写入。
- 规模:高峰期每秒约两千行,单行不超过 4KB。
- 服务要能重启后继续,不能因为重启丢掉已经收到但还没落盘的日志。
""".strip(),
    },
    {
        "system_nature": "无人值守的数据管道:接收 -> 去重 -> 落盘 -> 规则求值 -> 通知。"
                         "承重结构是日志行的唯一性判定、乱序与积压下的写入路径、"
                         "规则求值的时间窗口、以及通知的去重与重试。",
        "fixed_stack": "Python;Linux 服务器上以后台服务运行;无图形界面。"
                       "存储形态与进程拓扑在设计阶段点名后回收至此。",
        "quality_bar": "去重与保留策略必须有自动化测试;重启不丢数据是验收的一部分。"
                       "待确认:压测的基线配置。",
        "ui_conventions": "无界面项目,不适用。",
        "nfr_baseline": "高峰每秒约 2000 行,单行 <= 4KB;日志保留 30 天。"
                        "积压补发时不得阻塞实时写入(待确认具体指标)。",
        "boundaries": "always:本地落盘、重启可恢复。ask-first:引入任何外部消息队列、"
                      "数据库或新框架。never:把日志内容发到外部服务、静默丢弃日志行。",
    },
    [
        {"name": "服务骨架", "orientation": "foundation", "subdomain_type": "supporting",
         "foundation_kind": "architecture", "is_spike": False,
         "description": _desc("进程启动与退出、配置装载、模块接缝、存储读写边界、错误与诊断约定、测试脚手架",
                              "向上提供配置读取、存储读写、错误上报三类契约;不依赖任何业务模块",
                              "配置的唯一来源、存储位置与版本",
                              "不含接收协议、去重规则、告警规则或清理策略")},
        {"name": "日志接收与去重", "orientation": "vertical", "subdomain_type": "core",
         "is_spike": True,
         "description": _desc("接收日志行、在乱序与重发下判定唯一性、把已确认的行交给存储",
                              "向存储提供去重后的日志行;向上游返回是否已接收",
                              "唯一性判定所需的状态,是「这一行是否已收过」的唯一事实来源",
                              "不负责落盘格式、不负责规则求值、不负责清理")},
        {"name": "日志存储与查询", "orientation": "vertical", "subdomain_type": "supporting",
         "is_spike": False,
         "description": _desc("已确认日志行的落盘、按时间与来源的查询、到期清理",
                              "向命令行与规则求值提供只读查询",
                              "日志行本体及其保留期限,是日志内容的唯一事实来源",
                              "不判定重复、不产生告警")},
        {"name": "规则求值与告警", "orientation": "vertical", "subdomain_type": "core",
         "is_spike": False,
         "description": _desc("按配置的规则在时间窗口上求值、产生告警、对同一异常去重",
                              "向通知发送提供待发告警",
                              "告警状态与抑制窗口,是「这次异常是否已经报过」的唯一事实来源",
                              "不实现通知的传输与重试,不查询原始日志之外的东西")},
        {"name": "通知发送", "orientation": "vertical", "subdomain_type": "generic",
         "is_spike": False,
         "description": _desc("把告警送到内部消息接口,失败重试",
                              "接收待发告警,返回是否送达", "重试状态",
                              "不判定是否该告警、不做告警去重")},
        {"name": "命令行操作", "orientation": "vertical", "subdomain_type": "supporting",
         "is_spike": False,
         "description": _desc("值班人用的查询与运维子命令",
                              "只读消费查询契约与服务状态", "无",
                              "不修改日志、不改规则求值逻辑")},
    ],
    {
        "日志接收与去重": ["服务骨架"],
        "日志存储与查询": ["服务骨架", "日志接收与去重"],
        "规则求值与告警": ["服务骨架", "日志存储与查询"],
        "通知发送": ["服务骨架"],
        "命令行操作": ["服务骨架", "日志存储与查询"],
    },
)
