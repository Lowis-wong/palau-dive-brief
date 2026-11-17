# 文档索引（notes/）

这里放项目总文档的镜像，以及所有分析文档的位置。原始文档在 claude.ai 项目「帕劳潜水」里；仓库里这份是为了能和代码、提交记录一起追溯。镜像日期写在每个文件开头。

## 总文档

| 文件 | 内容 | 对应的 claude.ai 项目文档 |
|---|---|---|
| `工作历程.md` | 全项目时间线、架构、进度、待办、关键决策 | `palau-dive-brief-工作历程.md`、`claude/work-history.md`（两者内容相同） |
| `dive-brief-status.md` | 简报 App 的技术状态（英文，另一个对话维护，这里是快照） | `claude/dive-brief-status.md` |
| `roadmap.md` | 岸上分析项目路线图（1–12，11-17 版） | `palau-analysis-roadmap.md` |
| `lagoon-step1.md` | 潟湖增水 Step 1（数据） | `claude/lagoon-setup-step1.md` |
| `project2-feasibility.md` | 项目 2 自有潮汐常数的回测 | `claude/project2-feasibility.md` |

## 各项目的详细记录（在 `analysis/` 下）

| 项目 | 位置 | 对应的 claude.ai 项目文档 |
|---|---|---|
| Step 2 潟湖增水驱动因素 | `analysis/step2/README.md` | `claude/lagoon-setup-step2.md` |
| 项目 1 潟湖与外海潮汐差 | `analysis/project1/README.md` | `claude/project1-lagoon-vs-ocean.md` |
| 项目 3 卫星看水道口羽流 | `analysis/project3/PLAN.md`、`RESULTS.md` | `claude/project3-plan.md`、`claude/project3-results.md` |
| 项目 10 卫星测深 + 浮球图 | `analysis/project10/README.md`（总记录）、`docs/01–04` | `claude/project10-*.md` |

## 约定
- 新的分析项目：代码放 `scripts/analysis_<项目>/`，文档放 `analysis/<项目>/`，数据放 `analysis-data` 分支 `<项目>/`。
- 只改文档时放在 `analysis/` 或 `notes/`，不会触发分析工作流。
- claude.ai 项目里的文档更新后，同步更新这里的镜像。
