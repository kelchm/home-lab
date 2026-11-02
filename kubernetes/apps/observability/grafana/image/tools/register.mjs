import fs from 'node:fs';
import path from 'node:path';
import { register } from './prepare.mjs';

const input = path.resolve(process.argv[2]);
const source = path.resolve(process.argv[3]);
const read = (file) => JSON.parse(fs.readFileSync(path.join(input, file)));
const result = register(source, read('source-lock.json'), read('theme.json'));
fs.writeFileSync(path.join(input, 'registration.json'), JSON.stringify(result, null, 2) + '\n');
