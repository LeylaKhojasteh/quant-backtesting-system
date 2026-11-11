# Quant Backtest Lab — کد کامل نسخه تحویل‌شده

این پروژه همان سورس رابط کاربری نسخه آنلاین است، به‌همراه حالت تمام‌صفحه برای Price Chart و Trade Debugger. منبع داده فقط آرشیو ران‌های واقعی موتور Python است؛ هیچ حالت دمو یا داده ساختگی وجود ندارد.

## معماری جدید (نسخه اصلاح‌شده)

### مدل داده واحد: `BacktestRun`
همهٔ گزارش‌ها، نمودارها و متریک‌ها از یک قرارداد `BacktestRun` واحد (Zod schema) تغذیه می‌شوند. UI هیچ‌گاه مستقیماً به `trades`، `cumulative` یا ثابت‌های پراکنده وابسته نیست.

### تفکیک واحدها
- **Price terms**: PnL به واحدهای قیمت ابزار (خروجی موتور price-term).
- **Account terms**: PnL به ارز حساب، نیازمند contract size، lot sizing، commission، swap، slippage و تبدیل FX.

مقدارهای Account terms فقط وقتی `account.accountReady === true` موجودند. در ران‌های price-term همیشه `false` هستند، بنابراین Balance، Return٪، Sharpe، Sortino، Calmar، Annualized Return، Max Drawdown٪ و Modeling Quality به‌صورت **Pending / Not available** با علت دقیق نمایش داده می‌شوند.

### RunProvider
`RunProvider` در `app/layout.tsx` مونت شده و `useRun()` را در اختیار کل برنامه می‌گذارد. وضعیت‌های ممکن: `loading` → `ready` / `error` / `malformed`.

### Adapter Boundary
`loadArchivedRun()` در `adapters/python-result-adapter.ts` تنها entry point است: نتیجه `result.json` آرشیو Python را می‌خواند، با `mapPythonResultToBacktestRun()` به قرارداد `BacktestRun` تبدیل می‌کند و با Zod اعتبارسنجی می‌کند. اگر مانیفست یا ران در دسترس نباشد، UI حالت خالی یا خطای صریح نشان می‌دهد.

### زمان‌بندی
تمام زمان‌ها UTC و ISO-8601 با `Z` هست. تبدیل `datetime-local` → UTC ISO در `app/utc.ts` متمرکز است و offsetهای `+00:00` رد می‌شوند.

## اجرای محلی

پیش‌نیاز: Node.js نسخه 22.13 یا جدیدتر و pnpm نسخه 11.25.0.

داخل پوشه پروژه، در ترمینال VS Code اجرا کنید:

```bash
npm install --global pnpm@11.25.0
pnpm install --frozen-lockfile
pnpm dev
```

آدرس محلی که ترمینال نمایش می‌دهد را باز کنید (معمولاً http://localhost:5173).

برای ساخت نسخه اجرایی:

```bash
pnpm build
pnpm start
```

پروژه از React، TypeScript، Vinext/Vite و Tailwind استفاده می‌کند. اجرای محلی از پروفایل portable استفاده می‌کند.

## تمام‌صفحه

- دکمه مربع بازشونده کنار کنترل‌های Price Chart یا Trade Debugger را بزنید.
- با Esc یا همان دکمه به حالت عادی برگردید.
- در دیباگر، Candle Context و Tick Execution در حالت تمام‌صفحه هم قابل انتخاب‌اند.
- انتخاب معامله، تنظیمات زوم و لایه‌های نمودار حفظ می‌شوند؛ نمودار دوباره ساخته نمی‌شود.
- در محیط‌هایی که Fullscreen API مرورگر مجاز نیست، پنل تمام فضای صفحه وب را می‌گیرد.

## فایل‌های اصلی

- `app/layout.tsx`: ریشه برنامه، مونت `RunProvider`.
- `app/page.tsx`: پوسته برنامه، تنظیمات، نمودارها، لاگ معاملات و دیباگر.
- `app/fullscreen-panel.tsx`: تمام‌صفحه با پشتیبانی از Esc و حفظ حالت نمودار.
- `app/schema.ts`: قرارداد `BacktestRun` با Zod (runId، strategy، instrument، dateRange، dataSource، dataQuality، account، trades، equity، metrics، warnings، limitations) و فیلدهای اختیاری MAE/MFE هر معامله.
- `app/python-result.ts`: نگاشت `result.json` آرشیو Python به قرارداد `BacktestRun`.
- `app/utc.ts`: تبدیل/اعتبارسنجی/فرمت UTC ISO-8601 (z-terminated).
- `app/analytics.ts`: محاسبات derived (MAE/MFE، performance، risk، drawdown، annualisation gates، accountDrawdown).
- `app/run-context.tsx`: `RunProvider`، `useRun`، `useReadyRun`.
- `app/report.tsx`: گزارش کامل و نمودارهای تحلیل (FullReport، Analytics).
- `app/report-data.ts`: ۶۶ متریک گزارش، گروه‌بندی شده، Pending با دلیل.
- `app/analytics-section.tsx`: بخش‌های تحقیقاتی (Equity، Distribution، Direction، Exit، PerformanceQuality، DrawdownQuality، SignalQuality، EntryQuality، ExitQualityTable، RunIdentity).
- `app/charts.tsx`: CandleChart، TickChart، Equity (Cumulative PnL / Balance / Drawdown).
- `app/metrics-bar.tsx`: متریک‌های دسته‌بندی‌شده (Performance / Risk / Trade Statistics).
- `app/data-quality.tsx`: کیفیت داده اندازه‌گیری‌شده از `run.dataQuality`.
- `app/globals.css`: رنگ‌ها، چیدمان، واکنش‌گرایی.
- `components/ui`: اجزای تعاملی.
- `public/favicon.svg`: آیکون اختصاصی.
- `pnpm-lock.yaml`: نسخه‌های دقیق وابستگی‌ها.
- `tests/`: تست‌های واحد (utc، analytics، adapter) و اعتبارسنجی UI.
- `scripts/test.mjs`: رانر تست با esbuild + node --test.

## محدودیت‌های مدل Account

منبع داده فقط ران آرشیو‌شده موتور است. موارد زیر در ران‌های price-term باید Pending بمانند:

| متریک / فیلد | وضعیت | علت |
|-------------|-------|------|
| Balance | Not available | ارز حساب، موجودی اولیه، لات‌سایزینگ، PnL ارزی وجود ندارد |
| Return % | Pending | نیاز به سری موجودی ارزی |
| Max Drawdown % | Pending | نیاز به peak balance ارزی |
| Sharpe / Sortino / Calmar | Pending | نیاز به سری بازده دوره‌ای ارزی |
| Annualized Return | Pending | بازه نمونه < ۹۰ روز و < ۳۰ مشاهده |
| Modeling Quality | Not measured | موتور اعتبارسنجی داده متصل نیست |

### رفتار بک‌تست واقعی
- تنظیمات استراتژی از متادیتای همان استراتژی ساخته می‌شود و با `publish=true` به آرشیو و داشبورد می‌رود.
- اگر instrument برای نماد pip size نداشته باشد (مثلاً کریپتو)، SL/TP به‌صورت **price units** وارد و ارسال می‌شود؛ account model همچنان به instrument metadata نیاز دارد.
- metadata نمادها در `config/instruments.json` است و بدون تغییر کد قابل ویرایش/افزودن است. وضعیت complete/partial هر فایل ماهانه هم در `config/source_completeness.json` نگهداری می‌شود.
- تاریخ‌ها به‌صورت `DD/MM/YYYY` نمایش داده می‌شوند و مقدار ISO برای API ارسال می‌شود.
- MAE و MFE توسط موتور روی همان سمت خروج (BID برای LONG، ASK برای SHORT) و با لحاظ اسپرد محاسبه می‌شوند؛ MAE منفی و MFE مثبت است.
- خروجی CSV شامل metadata: `runId`، `strategyVersion`، `parameter snapshot`، `timezone=UTC`، `pnlUnit=price-terms` و ستون‌های `mae`/`mfe` است.

### اصطلاحات اصلاح‌شده
حذف شده: `MT5`، `MQL5`، `metatrader`، `OnTester`، `Strategy Tester Report`، `Field reference`.
جایگزین‌ها: `Strategy`، `Balance Regression Correlation`، `Custom Tester Output`.

## بررسی کیفیت و ساخت نسخه تولید

```bash
cd Quant-Backtest-Lab

# بررسی لینت
pnpm lint

# بررسی تایپ‌اسکریپت
pnpm exec tsc --noEmit

# اجرای تست‌ها (esbuild + node --test)
pnpm test

# ساخت نسخه تولید
pnpm build

# اجرای نسخه تولید
pnpm start
```

### تست‌ها
- `tests/utc.test.ts`: تبدیل/اعتبارسنجی UTC، رد offsetها.
- `tests/analytics.test.ts`: annualisation gates، accountDrawdown، advancedPerformance، risk (sharpe=null، maxDrawdownPct=null).
- `tests/adapter.test.ts`: نگاشت `result.json` آرشیو به `BacktestRun` و اعتبارسنجی Zod.
- `tests/ui.test.mjs`: اعتبارسنجی سطح سورس — عدم وجود دمو، عدم محاسبه موجودی ساختگی، اصطلاحات ممنوعه، caption price terms، CSV metadata، Pending در گزارش.

## محتوای ZIP

تمام فایل‌های سورس و تنظیمات پروژه موجودند. `node_modules`، خروجی build، کش‌ها، فایل‌های محرمانه و تاریخچه Git عمداً بسته‌بندی نشده‌اند؛ وابستگی‌ها با دستور نصب بالا ایجاد می‌شوند.