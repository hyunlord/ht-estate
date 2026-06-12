import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

// kakao-key-build-durable: post-build 키 단언 스크립트 단위 검증 — 빈-키(키리스) 빌드는 FAIL,
// 실 키 인라인 빌드는 PASS. 프로덕션에 키리스 번들이 조용히 배포되는 걸 차단하는 게이트.

const SCRIPT = join(__dirname, "..", "scripts", "assert-kakao-key.sh");
const KEY = "abcdef0123456789abcdef0123456789";

function runAssert(envFile: string, staticDir: string): { code: number; out: string } {
  try {
    const out = execFileSync("bash", [SCRIPT, envFile, staticDir], { encoding: "utf8" });
    return { code: 0, out };
  } catch (e) {
    const err = e as { status?: number; stdout?: string; stderr?: string };
    return { code: err.status ?? 1, out: `${err.stdout ?? ""}${err.stderr ?? ""}` };
  }
}

test.describe("assert-kakao-key.sh", () => {
  let dir: string;
  test.beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "kakao-assert-"));
  });
  test.afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("실 키 인라인 빌드 → PASS(exit 0)", () => {
    const env = join(dir, ".env");
    writeFileSync(env, `NEXT_PUBLIC_KAKAO_JS_KEY=${KEY}\n`);
    const stat = join(dir, "static");
    mkdirSync(stat);
    writeFileSync(join(stat, "chunk.js"), `var a="${KEY}";`); // 키 인라인됨
    const r = runAssert(env, stat);
    expect(r.code).toBe(0);
    expect(r.out).toContain("PASS");
  });

  test("키리스(빈 키) 빌드 → loud FAIL(exit 1)", () => {
    const env = join(dir, ".env");
    writeFileSync(env, `NEXT_PUBLIC_KAKAO_JS_KEY=${KEY}\n`);
    const stat = join(dir, "static");
    mkdirSync(stat);
    writeFileSync(join(stat, "chunk.js"), `var a="";`); // 키 미인라인(빈 export 상속)
    const r = runAssert(env, stat);
    expect(r.code).toBe(1);
    expect(r.out).toContain("FAIL");
  });

  test("env에 키 없음 → FAIL(exit 1)", () => {
    const env = join(dir, ".env");
    writeFileSync(env, `NEXT_PUBLIC_KAKAO_JS_KEY=\n`);
    const stat = join(dir, "static");
    mkdirSync(stat);
    writeFileSync(join(stat, "chunk.js"), `var a="${KEY}";`);
    const r = runAssert(env, stat);
    expect(r.code).toBe(1);
    expect(r.out).toContain("FAIL");
  });
});
