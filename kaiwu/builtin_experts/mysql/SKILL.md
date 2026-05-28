---
name: MySQLExpert
version: 1.0.0
trigger_keywords: [mysql, 数据库, 建表, 索引, 查询, 连接池, pymysql, 存储过程, 分库分表]
trigger_min_confidence: 0.7
pipeline: [locator, generator, verifier]
lifecycle: mature
---

## 领域知识

- 建表规范：InnoDB引擎，utf8mb4字符集，主键BIGINT AUTO_INCREMENT，字段加NOT NULL和DEFAULT
- 索引设计：WHERE/JOIN/ORDER BY列建索引，联合索引遵循最左前缀原则，区分度高的列放前面
- 覆盖索引：SELECT列都在索引中避免回表（Extra显示Using index）
- 避免索引失效：不在索引列上用函数或运算、不用前导模糊LIKE、注意隐式类型转换
- SQL注入防护：Python用参数化查询（%s占位符），绝不拼接用户输入的SQL字符串
- 连接池：SQLAlchemy用QueuePool（pool_size=5, max_overflow=10），pymysql加connect_timeout和read_timeout
- 事务：写操作加try/except/rollback/finally(close)，只读查询不开启事务
- 慢查询：EXPLAIN分析执行计划，type列至少range级别，避免ALL全表扫描
- 分页优化：深分页用游标方式WHERE id > last_id LIMIT n代替LIMIT+OFFSET
- 批量操作：INSERT批量VALUES每批500-1000条，UPDATE用CASE WHEN
- 表设计：字段类型最小化满足需求（用INT不用BIGINT，用VARCHAR不用TEXT），避免NULL列
- 读写分离：写走主库，读走从库，SQLAlchemy用binds路由
- 迁移工具：Alembic管理schema版本，UP/DOWN都要实现，autogenerate后手动检查
- Python连接：优先用SQLAlchemy ORM做CRUD，复杂查询用原生SQL（text()），不混用裸cursor和ORM
- 监控：开启slow_query_log，long_query_time设为1秒，定期检查没走索引的查询

## 经验规则（自动生成）
