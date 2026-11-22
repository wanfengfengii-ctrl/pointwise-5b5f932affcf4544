# 原采样位复勘

值班员首先按 README.md 受理原源批并开户。计量员仍可用 `post` 指定站日绝对总量，这笔调整的净差留在日账上。复勘只替换原开户批的一个采样位。原批的序位基于全部采样行，缺测行也占位。

## 受理与过账

从 `warehouse` 导入以下两个函数。继续使用原有 `Warehouse`、`StationDay`、`Measurement`、`SourceReference` 和 `storage` 的业务异常。

- `admit_resurvey(w, day, batch_id, position, id, measurement)` 返回不可变的受理件，字段为 `day`、`batch_id`、`position`、`resurvey_id`、`measurement`。`batch_id` 须是该站日已开户的原批号，`position` 为从 1 开始的整数且不能是布尔值，`measurement` 遵循原采样规则，出处须属同站。无效开户批、位置、编号或采样抛 `InvalidInput`。复勘号在全仓复勘业务内唯一，与普通总量更正号分开。同号同参复领原受理件，同号异参抛 `Conflict`。出处属于受理身份，同值换出处仍是异参。成功受理的证据在重开后可复领。受理仅形成待办，不占正式序号。
- `post_resurvey(w, day, id, region)` 返回不可变的首执，字段为 `day`、`batch_id`、`position`、`resurvey_id`、`before`、`after`、`sequence`、`issued_region`、`issued_epoch`、`old_litres`、`new_litres`、`delta`。`before` 和 `after` 是实际前后两条 `Measurement`。本站日未受理过该号时抛 `NotFound`。已有正式首执可在重开并关闭原签发区后从当前账复领，仍返回原签发区、代次和实际前后像。首次过账由当前承办区在该代次有权签发时办理。旧区结清在先则迟到调用抛 `NotOwner`，原受理号可在新区办理。

正式净差为新采样计量减去该位过账前最近一次正式采样计量，无正式复勘则用原位采样。`None` 的计量为零，读回仍为缺测，`0` 仍是实测。净差加到过账前实际日值上。即使为零，也与普通调整共用同一个递增正式序号。过账一次固定账头、分录、实际采样前后像与首执。普通总量更正仍直接设置绝对日值，既有净差留作未分摊的人工调整。换区移交结清时已正式成立的复勘及首执，受理待办仍可由新区办理。

## 账面与固定档案

`reconcile_day` 和 `get_frozen` 保留原返回字段，并提供 `source_litres`（该前沿所有有效采样之和）、`adjustment_litres`（人工总量更正净差之和）和 `resurveys`（按正式序号排列的复勘首执元组）。当前或冻结升数等于前两项之和。`postings` 仍按同一正式顺序列出所有分录。复勘分录的展示字段 `correction_id` 为复勘号，用 `sequence` 与 `resurveys` 对应。两类号可以相同，各自的记录和首执均可辨认。

在账头、正式分录及首执都相符的同时，核账和换区实收也须核对复勘证据的开户批、位置、实际旧像和净差。冻结保存当时正式账面所见的原序源批和复勘依据，按号从档案独立领取。同一站日的原批不随复勘改动。原格式冻结件按其原批和旧分录取得上述字段，`resurveys` 为空元组。

## 回调与样例

用现有 `Warehouse(events=...)` 观察 `resurvey_prepared` 和 `resurvey_committed` 两个事件。前者在首次办理已取得受理证据并选定承办代次后、正式入账事务之前发出，载荷为受理件。后者在首次正式提交后、返回首执前发出，载荷为首执。已有首执的复领直接返回。原换区的 `candidate_copied` 仍表示旧区候选账像已经复制，随后才结清旧区。

- `python3 -B resurvey_cases.py inputs` 展示两组复勘输入。原开户批使用 `examples/shift.json`。
- `python3 -B resurvey_cases.py joint` 在功能实现后运行联合样例。最初冻结为 7 L。普通更正 C1 调至 11 L。先受理 R2（第三位改为 2 L），再受理 R1（第三位改为缺测）。仅受理时冻结为 11 L，复勘为空。R2 的旧区过账调用暂停，触发换区。候选复制后 R1 在旧区正式入账并丢失回包。换区完成，R2 原调用恢复并得到 `NotOwner`。重开关闭东区后，西区账面为源计量 3 L、人工调整 +4 L、现值 7 L。R1 仍领取东区原首执，序号 2，净差 −4 L。西区 R2 的实际旧像为 R1 的缺测件，序号 3，净差 +2 L，冻得源计量 5 L、人工调整 +4 L、现值 9 L。三个冻结件各自保留当时的原序和正式证据，可关闭账区、目录和源库后逐号领取。
- `python3 -B resurvey_cases.py zero` 在功能实现后运行零差样例。第二位缺测改为 0 L。用同号提交同值异出处抛 `Conflict`。原受理件过账仍占序号 1、净差 0 L，冻结显示原缺测、正式旧缺测及带新出处的实测 0 L。

沿用 `.pointwise-runtime.json` 的 `version=1`、`kind=cli` 与空 `setup`。`verify` 和 `demo` 各使用一组命令参数。验收继续运行原回归，并核对上述复勘顺序、首执、冻结独立领取及旧冻结兼容。演示展示原位复勘在换区前后的实账和冻结结果。
