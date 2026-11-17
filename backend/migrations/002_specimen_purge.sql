-- 保密试件清除：specimen_purges 为清除任务状态机（purging → completed）。
-- reference 唯一，同时充当永久墓碑：样本追加、任务冻结与演示 seed 均拒绝或跳过该编号。
-- copies 保存全部副本去向清单（来源样本/冻结输入/结果/结果文件/工艺备忘/在途下载）与未清完原因；
-- 仅当核对全部副本消失后，state 才由 purging 转为 completed。
CREATE TABLE IF NOT EXISTS specimen_purges (
    id SERIAL PRIMARY KEY,
    reference VARCHAR(120) NOT NULL UNIQUE,
    state VARCHAR(20) DEFAULT 'purging',
    reason TEXT DEFAULT '',
    copies JSON NOT NULL,
    targets JSON NOT NULL,
    created_by VARCHAR(120) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    completed_at VARCHAR(40) DEFAULT ''
);
