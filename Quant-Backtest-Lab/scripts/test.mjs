/**
 * Test runner for Quant Backtest Lab.
 * Uses esbuild to bundle TypeScript test files and runs them with node --test.
 */
import { readdir, mkdir, rm, copyFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { createRequire } from 'node:module';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '..');
const testsDir = join(root, 'tests');
const outDir = join(root, '.test-build');

async function findEsbuild() {
  const require = createRequire(import.meta.url);
  // Try hoisted esbuild first
  try {
    return require('esbuild');
  } catch {
    // Fall back to pnpm store
    const pnpmEsbuildDir = join(root, 'node_modules', '.pnpm');
    try {
      const entries = await readdir(pnpmEsbuildDir);
      const esbuildDirs = entries.filter(e => e.startsWith('esbuild@')).sort().reverse();
      for (const dir of esbuildDirs) {
        try {
          return require(join(pnpmEsbuildDir, dir, 'node_modules', 'esbuild'));
        } catch {
          continue;
        }
      }
    } catch {
      // ignore
    }
    throw new Error('esbuild not found');
  }
}

async function main() {
  // Clean and create output directory
  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  const esbuild = await findEsbuild();
  console.log('Found esbuild');

  // Find all .test.ts files
  const testFiles = (await readdir(testsDir))
    .filter(f => f.endsWith('.test.ts'))
    .map(f => join(testsDir, f));

  if (testFiles.length === 0) {
    console.log('No test files found');
    process.exit(0);
  }

  console.log(`Building ${testFiles.length} test file(s)...`);

  // Bundle each test file
  await Promise.all(testFiles.map(async (file) => {
    const baseName = (file.split(/[\\/]/).pop() || '').replace(/\.test\.ts$/, '');
    await esbuild.build({
      entryPoints: [file],
      bundle: true,
      platform: 'node',
      format: 'esm',
      outfile: join(outDir, `${baseName}.test.mjs`),
      external: [], // bundle everything
      target: 'node22',
      sourcemap: false,
      treeShaking: true,
    });
  }));

  // Also copy ui.test.mjs if it exists (plain JS test)
  const uiTestSrc = join(testsDir, 'ui.test.mjs');
  try {
    await copyFile(uiTestSrc, join(outDir, 'ui.test.mjs'));
  } catch {
    // ignore if not present
  }

  console.log('Running tests...');
  
  // Run node --test on the built files
  const builtFiles = (await readdir(outDir))
    .filter(f => f.endsWith('.test.mjs'))
    .map(f => join(outDir, f));

  const result = spawnSync(process.execPath, ['--test', ...builtFiles], {
    cwd: root,
    stdio: 'inherit',
    env: { ...process.env, NODE_OPTIONS: '--experimental-vm-modules' },
  });

  process.exit(result.status ?? 1);
}

main().catch(err => {
  console.error('Test runner failed:', err);
  process.exit(1);
});