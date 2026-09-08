import assert from 'node:assert/strict'
import { parseWakePhrase } from '../web/src/lib/wake.ts'

assert.equal(parseWakePhrase('今天天气怎么样'), null)
assert.deepEqual(parseWakePhrase('你好，通通！'), {text:''})
assert.deepEqual(parseWakePhrase('你好通通，今天天气怎么样？'), {text:'今天天气怎么样'})
assert.deepEqual(parseWakePhrase('请听一下，你好通通，每天八点半提醒我吃药。'), {text:'每天八点半提醒我吃药'})
assert.equal(parseWakePhrase('你好同学'), null)
console.log('PASS wake phrase gating and command extraction (5 cases)')
