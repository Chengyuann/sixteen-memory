// Test-only RPC instrumentation; production entrypoint never imports this file.
import worker, { UserMemory as ProductionMemory } from "./index";
export default worker;

export class UserMemory extends ProductionMemory {
  private cursors: SqlStorageCursor<Record<string, SqlStorageValue>>[] = [];
  private rawSql: SqlStorage;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.rawSql = ctx.storage.sql;
    const cursors = this.cursors;
    const raw = this.rawSql;
    Reflect.set(this, "sql", new Proxy(raw, {
      get(target, property) {
        if (property === "exec") {
          return (...args: Parameters<SqlStorage["exec"]>) => {
            const result = target.exec(...args);
            cursors.push(result);
            return result;
          };
        }
        const result = Reflect.get(target, property);
        return typeof result === "function" ? result.bind(target) : result;
      },
    }));
  }

  async testStats() {
    const sql = this.rawSql;
    const cursors = this.cursors.splice(0);
    return {
      operations: cursors.length,
      rowsRead: cursors.reduce((n, c) => n + c.rowsRead, 0),
      rowsWritten: cursors.reduce((n, c) => n + c.rowsWritten, 0),
      chunks: sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM chunks").one().n,
      indexed: sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM search_index").one().n,
      writes: sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM writes").one().n,
      counter: Number(sql.exec<{ value: string }>(
        "SELECT value FROM meta WHERE key='chunk_count'",
      ).one().value),
      bytes: sql.databaseSize,
      alarm: await this.ctx.storage.getAlarm(),
    };
  }

  testExpire(requestId: string) {
    this.rawSql.exec("UPDATE chunks SET expires=0 WHERE request_id=?", requestId);
    this.rawSql.exec("UPDATE writes SET expires=0 WHERE request_id=?", requestId);
  }

  async testDeleteAlarm() { await this.ctx.storage.deleteAlarm(); }
  async testAlarm() { await super.alarm(); }
  testDeleteCounter() { this.rawSql.exec("DELETE FROM meta WHERE key='chunk_count'"); }
}
