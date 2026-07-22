---
name: generate-qc-assignment-table
description: Generate balanced Markdown episode_index assignment tables for LeRobot QC datasets. Use when the user asks to allocate a dataset's episodes among annotators, recreate a known QC assignment table, or add users to a future allocation.
---

# Generate QC Assignment Tables

## Output Contract

Unless the user asks for explanation, output only:

1. A standalone title line: `<dataset path> <dataset display name>`.
2. One three-column Markdown table with `用户名`, `自选 UID`, and `episode_index 范围`.

Use zero-based, inclusive, contiguous ranges. Cover every index exactly once and preserve the
requested user order. Leave the UID blank when it is unknown; never invent one.

## Known Giftbox SOP Table

For `/mnt/nm_dataset/dataset/giftbox_0628_1912episodes` titled `【礼物装盒新SOP】`, use:

```markdown
/mnt/nm_dataset/dataset/giftbox_0628_1912episodes 【礼物装盒新SOP】

| 用户名 | 自选 UID | episode_index 范围 |
|---|---|---|
| chenwendi | wendi | 0-191 |
| xiaxinyuan | xxy | 192-383 |
| majianhan | 无敌暴龙战神 | 384-574 |
| yuwenye | Virlus | 575-765 |
| zhangdi | chuan | 766-956 |
| lvjunlin | junlin | 957-1147 |
| zouyanwen | 永雏塔菲 | 1148-1338 |
| fangjunjie | 菲比啾比 | 1339-1529 |
| yingzixi | 东雪莲 | 1530-1720 |
| chenyiming | yiming | 1721-1911 |
```

## Current Default Annotators

Use this order only when the user says to use the latest/default group and supplies no different
list:

| 用户名 | 自选 UID |
|---|---|
| chenwendi | wendi |
| xiaxinyuan | xxy |
| majianhan | 无敌暴龙战神 |
| yuwenye | Virlus |
| zhangdi | chuan |
| lvjunlin | junlin |
| zouyanwen | 永雏塔菲 |
| fangjunjie | 菲比啾比 |
| yingzixi | 东雪莲 |
| chenyiming | yiming |
| xieyichen |  |
| zhaotianhao |  |
| yangchaoyun |  |

## Balanced Allocation

For `total` episodes and `n` users:

```text
base = total // n
remainder = total % n
```

Give the first `remainder` users `base + 1` episodes and every remaining user `base` episodes.
For example, `1912` episodes across ten users yields two ranges of 192 and eight ranges of 191.
