/**
 * LOCAL TEST FIXTURE ONLY. Not referenced by the production Wrangler config.
 * Exposes storage controls solely inside the isolated Miniflare test runtime.
 */
import { UserMemory as ProductionMemory } from "../src/index";
export { default } from "../src/index";

export class UserMemory extends ProductionMemory {
  async testExpireAndSchedule() {
    this.ctx.storage.transactionSync(() => {
      this.ctx.storage.sql.exec("UPDATE chunks SET expires=0");
      this.ctx.storage.sql.exec("UPDATE writes SET expires=0");
    });
    await this.ctx.storage.setAlarm(Date.now() + 200);
    await this.ctx.storage.sync();
  }

  async testStorageCounts() {
    const sql = this.ctx.storage.sql;
    return JSON.stringify({
      chunks: sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM chunks").one().n,
      writes: sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM writes").one().n,
      indexed: sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM search_index").one().n,
      alarm: await this.ctx.storage.getAlarm(),
    });
  }
}
