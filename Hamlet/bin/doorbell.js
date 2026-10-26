#!/usr/bin/env node
if(process.argv[2]!=='ring')process.argv.splice(2,0,'ring');
await import('../packages/cli/dist/index.js');
