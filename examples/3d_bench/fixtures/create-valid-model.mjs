import { readFile, writeFile } from 'node:fs/promises';

const [templatePath, bodyPath, outputPath] = process.argv.slice(2);
if (!templatePath || !bodyPath || !outputPath) {
  throw new Error('Usage: node create-valid-model.mjs TEMPLATE BODY OUTPUT');
}
const template = await readFile(templatePath, 'utf8');
const body = await readFile(bodyPath, 'utf8');
const marker = 'export function createModel(THREE) {';
const start = template.indexOf(marker);
if (start < 0 || template.indexOf(marker, start + marker.length) >= 0) {
  throw new Error('Template must contain exactly one createModel entry point');
}
const bodyStart = body.indexOf(marker);
if (bodyStart < 0) throw new Error('Body fixture must contain createModel');
await writeFile(outputPath, template.slice(0, start) + body.slice(bodyStart));
