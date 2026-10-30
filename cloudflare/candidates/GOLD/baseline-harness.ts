// Test-only baseline instrumentation; never deployed.
import worker, { UserMemory as BaselineMemory } from "../BASE/index";
export default worker;

export class UserMemory extends BaselineMemory {
  private cursors: SqlStorageCursor<Record<string, SqlStorageValue>>[] = [];
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    const cursors = this.cursors;
    Reflect.set(this, "sql", new Proxy(ctx.storage.sql, {
      get(target, property) {
        if (property === "exec") {
          return (...args: Parameters<SqlStorage["exec"]>) => {
            const result = target.exec(...args); cursors.push(result); return result;
          };
        }
        const result = Reflect.get(target, property);
        return typeof result === "function" ? result.bind(target) : result;
      },
    }));
  }
  testStats() {
    const cursors = this.cursors.splice(0);
    return {
      operations: cursors.length,
      rowsRead: cursors.reduce((n, c) => n + c.rowsRead, 0),
      rowsWritten: cursors.reduce((n, c) => n + c.rowsWritten, 0),
    };
  }
}
