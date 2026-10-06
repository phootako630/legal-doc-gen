// 用 Playwright 走完 上传 → AI 处理 → 审核 → 预览 四步，每步截图；同时保存分析结果与生成的诉状文本。
//
// 用法：node drive.cjs <输出目录> <文件1> [文件2 ...] [--resume]
//   --resume：遇到断点（「提交并继续」）时按页面默认值提交并继续；不加则截图后停止，交给人判断。
// 输出（输出目录下）：01-upload.png … 06-preview-full.png、04-pending-N.png、
//   analyze.json（/api/analyze 与 /api/resume 的最后一次响应）、complaint.txt（/api/generate 生成的全文）。
// 文件名会影响材料类型识别：建议含「审批表」「合同」「检验报告 / 验收报告」等字样。
const fs = require('fs');
const path = require('path');

const PLAYWRIGHT = process.env.PLAYWRIGHT_MODULE || '/opt/node22/lib/node_modules/playwright';
const { chromium } = require(PLAYWRIGHT);

const args = process.argv.slice(2);
const resume = args.includes('--resume');
const [outDir, ...files] = args.filter((a) => a !== '--resume');
if (!outDir || files.length === 0) {
  console.error('用法：node drive.cjs <输出目录> <文件1> [文件2 ...] [--resume]');
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });

const MIME = {
  '.pdf': 'application/pdf',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
};
const URL = process.env.APP_URL || 'http://localhost:5173';
const ANALYSIS_TIMEOUT_MS = 15 * 60 * 1000;

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
  page.on('pageerror', (e) => console.log('页面报错', e.message));
  page.on('response', async (res) => {
    const u = res.url();
    try {
      if (/\/api\/(analyze|resume)$/.test(u)) {
        fs.writeFileSync(path.join(outDir, 'analyze.json'), await res.text());
        if (res.status() >= 400) console.log('分析接口返回', res.status(), (await res.text()).slice(0, 300));
      } else if (/\/api\/generate$/.test(u) && res.ok()) {
        const body = await res.json();
        // 去掉填空标记 ⟦⟧，便于与律师诉状逐句对比
        fs.writeFileSync(path.join(outDir, 'complaint.txt'), body.complaint_text.replace(/[⟦⟧]/g, ''));
      }
    } catch {
      // 响应体读取失败不影响页面流程
    }
  });
  const shot = (name, full = true) =>
    page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: full });

  await page.goto(URL);
  await page.waitForTimeout(1500);
  await shot('01-upload');

  // 必须用 buffer + 显式 mimeType：只给路径时 File.type 可能为空，前端会以「格式不支持」拒收
  await page.locator('input[type=file]').first().setInputFiles(
    files.map((f) => ({
      name: path.basename(f),
      mimeType: MIME[path.extname(f).toLowerCase()] || 'application/octet-stream',
      buffer: fs.readFileSync(f),
    })),
  );
  await page.waitForTimeout(1500);
  await shot('02-selected');
  await page.getByRole('button', { name: '开始分析' }).click();
  await page.waitForTimeout(15000);
  await shot('03-processing');

  const t0 = Date.now();
  let pauses = 0;
  for (;;) {
    if (await page.getByRole('button', { name: /确认并生成起诉状/ }).count()) break;
    if (await page.getByRole('button', { name: '提交并继续' }).count()) {
      pauses += 1;
      await shot(`04-pending-${pauses}`);
      console.log(`第 ${pauses} 次断点（截图 04-pending-${pauses}.png）`);
      if (!resume) {
        console.log('未加 --resume，停在断点处');
        await browser.close();
        return;
      }
      await page.getByRole('button', { name: '提交并继续' }).click();
      await page.waitForTimeout(5000);
      continue;
    }
    if (Date.now() - t0 > ANALYSIS_TIMEOUT_MS) {
      await shot('timeout');
      console.log('等待分析超时（15 分钟），见 timeout.png 与后端日志');
      await browser.close();
      process.exit(1);
    }
    await page.waitForTimeout(3000);
  }
  console.log(`分析用时 ${Math.round((Date.now() - t0) / 1000)} 秒，断点 ${pauses} 次`);

  await page.waitForTimeout(1000);
  await shot('05-review-top', false);
  await shot('05-review-full');
  await page.getByRole('button', { name: /确认并生成起诉状/ }).click();
  await page.waitForSelector('text=下载', { timeout: 120000 });
  await page.waitForTimeout(1500);
  await shot('06-preview-top', false);
  await shot('06-preview-full');
  await browser.close();
  console.log('完成，输出在', outDir);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
