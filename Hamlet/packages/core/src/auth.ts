import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { join } from "node:path";
import { AppError, type Actor } from "./types.js";
export class Auth {
  secret: Buffer;
  ownerToken: string;
  constructor(dir: string) {
    const p = join(dir, "secret");
    try {
      this.secret = readFileSync(p);
    } catch {
      this.secret = randomBytes(32);
      writeFileSync(p, this.secret, { mode: 0o600, flag: "wx" });
    }
    chmodSync(p, 0o600);
    this.ownerToken = this.sign({ kind: "owner" });
  }
  sign(actor: Actor) {
    const data = Buffer.from(JSON.stringify(actor)).toString("base64url");
    return `${data}.${createHmac("sha256", this.secret).update(data).digest("base64url")}`;
  }
  verify(token: string): Actor {
    const [d, s] = token.split(".");
    if (!d || !s) throw new AppError("身份认证失败", 401);
    const mac = createHmac("sha256", this.secret).update(d).digest();
    const sig = Buffer.from(s, "base64url");
    if (sig.length !== mac.length || !timingSafeEqual(sig, mac))
      throw new AppError("身份认证失败", 401);
    try {
      const a = JSON.parse(Buffer.from(d, "base64url").toString());
      if (!["owner", "instance", "doorbell"].includes(a.kind)) throw Error();
      return a;
    } catch {
      throw new AppError("身份认证失败", 401);
    }
  }
}
