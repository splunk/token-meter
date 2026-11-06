import json
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class DashboardFilterRefreshTests(unittest.TestCase):
    def test_slow_filter_requests_survive_live_refresh_and_new_selections_win(self):
        root = Path(__file__).resolve().parents[1]
        script = r"""
const fs=require('fs'),root=process.argv[1];
const source=fs.readFileSync(root+'/dashboard/js/work-insights.js','utf8');
const live=fs.readFileSync(root+'/dashboard/js/live.js','utf8');
const part=(s,a,b)=>s.slice(s.indexOf(a),s.indexOf(b,s.indexOf(a)));
let workRequest=0,workLastLoadedAt=0,workMonths='6',workRuntime='',workProject='';
let deliveryRequest=0,deliveryLastLoadedAt=0,deliveryRange='30',deliveryProject='',deliveryLoadRetries=0,deliveryRetryTimer=0;
let workLoading=false,deliveryLoading=false,active='work',renders=[],pending=[];
let RUNTIME_CATALOG={},defaultSessionBudget=0,LATEST=null,pinned=null,currentPanel='',sessionScope='',goalsLoadedAt=Date.now(),workSupported=true;
const no=()=>{},adoptFreshDiscounts=no,applyWorkSupport=no,renderMenubarSettings=no,renderCurrentSessions=no,maybeAutoShowWelcome=no;
const nodes={};function $(id){return nodes[id]||(nodes[id]={classList:{contains:()=>id==='view-'+active},open:false});}
function fetch(url){return new Promise(resolve=>pending.push({url,resolve}));}
function renderWork(p){renders.push('work:'+p.value);} function renderDelivery(p){renders.push('git:'+p.value);}
function answer(index,value){pending[index].resolve({ok:true,json:async()=>({ok:true,value,coverage:{last_checked:true}})});}
const setTimeout=no,clearTimeout=no;
eval(part(source,'async function loadWork(){','async function postWorkAction(')+part(source,'async function loadDelivery(){',"$('d-coverage').addEventListener")+part(live,'function applyLiveState(state){','async function refreshLiveState(){')+`
(async()=>{
 active='work';let old=loadWork();workMonths='1d';let chosen=loadWork();applyLiveState({});applyLiveState({});
 let workCount=pending.length;answer(0,'old');answer(1,'chosen');await Promise.all([old,chosen]);
 active='git';let before=pending.length;let oldGit=loadDelivery();deliveryRange='7';let chosenGit=loadDelivery();applyLiveState({});applyLiveState({});
 let gitCount=pending.length-before;answer(before,'old');answer(before+1,'chosen');await Promise.all([oldGit,chosenGit]);
 console.log(JSON.stringify({workCount,gitCount,renders}));
})().catch(e=>{console.error(e);process.exitCode=1;});`);
"""
        result = subprocess.run(["node", "-e", script, str(root)], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"workCount": 2, "gitCount": 2,
                                                     "renders": ["work:chosen", "git:chosen"]})
