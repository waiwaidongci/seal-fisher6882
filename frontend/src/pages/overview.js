import {DOMAIN} from '../constants/domain.js';
import {store} from '../stores/research.js';
import {esc} from '../utils/format.js';
import {runList} from '../components/common/run-list.js';
import {table} from '../components/common/table.js';
export function render(){return `<div class="eyebrow">FIELD NOTES / ${DOMAIN.direction}</div><section class="hero"><div><h1>${DOMAIN.title}</h1><p class="lead">${DOMAIN.scenario}</p><a class="button" href="#analysis">发起新的研究 →</a></div><aside><span class="folio">${DOMAIN.id.slice(-2)}</span><p>${DOMAIN.algorithm}</p><small>固定种子42 · 真实Python计算 · 输入快照可追溯</small></aside></section><section class="strip"><span>${store.studies.length} 个${DOMAIN.study_label}</span><span>${store.studies.reduce((n,s)=>n+s.row_count,0)} 条业务观测</span><span>${store.runs.length} 次计算记录</span><span>${store.batches.filter(b=>b.status==='pending').length} 个待发布批次</span></section><h2>研究进度 <small>从来源到复核的同一条链路</small></h2>${runList()}<h2>来源索引</h2>${table([DOMAIN.study_label,'类型','来源文件 / 引用','观测数'],store.studies.map(s=>[s.name,s.kind==='joint'?'联合研究':'来源批次',s.kind==='joint'?`引用 ${s.member_count} 个批次`:s.source_filename,s.kind==='joint'?'去重汇总':s.row_count]))}`;}
export function bind(){}
