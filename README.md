# 澄浦河口监测日仓

Python 3.11 标准库与 SQLite。早班值班员验收测站源批并开立站日，计量员修正站日读数，换区经办员转交承办，交班员核对逐笔净差，审计员按冻结号领取当时的观测和来历。

## 运行

在项目根目录运行完整回归。

```sh
python3 -B check_project.py
```

运行临时数据目录中的完整工作剧本。

```sh
python3 -B -m warehouse demo
```

可用 `python3 -B -m warehouse init-sample ./sample-data` 持久受理并开户。重复初始化领取原受理件和初账。业务更正、当前承办及既有冻结成果继续保留。`examples/shift.json` 保存样例源批，目录默认配置 `east` 和 `west`。

## Python 接口

从 `warehouse` 导入 `Warehouse` 和下表业务函数。`Warehouse(path)` 是可关闭、可重开的存储句柄，支持 `with`。从 `intake.stations` 导入 `StationDay(station, date)` 和 `SourceReference(station, record, locator)`。日期用 `YYYY-MM-DD`。站号、区域、批号及办理号使用 1 至 64 个 ASCII 字母、数字、下划线或连字符，首字符为字母或数字。`SourceBatch(batch_id, day, measurements)` 的有序明细使用 `Measurement(litres, source)`，从 `intake.batches` 和 `intake.measurements` 导入。出处站号须与站日一致，原记录及定位字段须为非空白文本。

| 函数 | 返回值与规则 |
| --- | --- |
| `admit(w, batch)` | `AcceptedBatch`，含受理号、原序源批和 `valid_measurements`。批号绑定整批内容和出处。 |
| `open_day(w, batch_id, region)` | `OpenedDay`，含站日、批号、初始区及初值。按原批及原初始区重投领取初账。 |
| `post(w, day, correction_id, region, litres)` | `PostingReceipt`，含实际旧值、新值、净差、正式序号和原签发区及代次。办理号在更正业务内唯一，绑定站日和升数。已有首执从当前正式账领取。新办理要求所传区是当前承办区。 |
| `move(w, day, move_id, target)` | `MoveResult`，含新旧承办区及代次和已交接正式分录数。同号同参可续办或复领，每个站日同时最多一笔在办换区。 |
| `reconcile_day(w, day)` | `ShiftReport`，含承办区、初值、现值、按提交次序排列的分录及净差合计。 |
| `freeze(w, day)` | `FreezeResult`，含冻结号、站日及冻结升数。每次成功冻结分配独立编号。 |
| `get_frozen(w, number)` | `FrozenFacts`，含原序源批、初值、冻结时现值、承办区代次及所见正式分录。 |

计量和差额均为精确整数升数。布尔值无计量资格。`None` 表示有出处的缺测，`0` 是有效观测。零差更正也占一个正式序号。分别保存目录、各区账、源批和冻结档案。初始化先登记待发布承办，再写区域初账，接着发布。换区期间目标先接收候选，正式交接从旧区本地结清开始。结清前已成立的更正随其首执一起移交，换区自身增加零笔计量分录。目录发布后可用 `w.close_region('east')` 关闭旧区句柄，西区仍可继续办理。冻结档案提交后可独立领取。

`storage` 定义 `InvalidInput`、`Conflict`、`NotFound`、`NotOwner`、`Unavailable` 和 `InconsistentBook`。`reconcile.discrepancies.ReconciliationError` 报告不完整或不一致的正式账。存储失败保留 SQLite 异常供调用者区分。跨库操作中断可能留下合法的待办，按原参数重投继续处理。`Warehouse(events=callback)` 可接收本地事务结束后的 `(event, payload)`，用于按明确顺序观察和暂停操作。

## 样例核账

早班两条有效采样 3 L 和 4 L 合计 7 L，夹在其中的缺测仍有出处。开户后冻结 7 L。东区正式更正 C1 到 11 L，记录 +4 L。西区接手后 C2 到 16 L，记录 +5 L，交班核得两笔合计 +9 L。西区可复领原东区首执，冻结号仍领取 7 L。若东区请求在换区生效时仅完成准备，西区从 7 L 起账，C2 为 +9 L。回归还覆盖源批冲突、零升、负净差、重开续办、旧区迟到、冻结写入回滚及账链断裂。
