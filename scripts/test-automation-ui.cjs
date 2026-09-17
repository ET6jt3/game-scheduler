// Harmless browser acceptance: create disabled fixture chains, never launch games.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
(async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'gs-ui-'));fs.mkdirSync(path.join(root,'Data'));
 const port=18187,url='http://127.0.0.1:'+port;
 fs.writeFileSync(path.join(root,'config.json'),JSON.stringify({addr:'127.0.0.1:'+port,db_path:path.join(root,'Data','state.db'),data_dir:path.join(root,'Data'),max_concurrent:1,monitor_enabled:false}));
 const server=spawn(path.resolve('dist/ui-server'),['-config',path.join(root,'config.json')],{stdio:'ignore'});
 let browser;
 try {
  let ready=false;for(let i=0;i<100;i++){try{const r=await fetch(url+'/healthz');if(r.ok){ready=true;break}}catch{}await new Promise(r=>setTimeout(r,100))}if(!ready)throw Error('server did not become ready');
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  for(const [i,name]of ['BetterGI','HSR','NTE'].entries()){
   let r=await page.request.post(url+'/api/games',{data:{id:'ui-'+i,name,adapter:'genshin',tool_path:'/bin/true',enabled:true}});if(!r.ok())throw Error(await r.text());
   r=await page.request.post(url+'/api/tasks',{data:{game_id:'ui-'+i,name:name+' daily',type:'raw',params:JSON.stringify({raw_args:[]}),enabled:true}});if(!r.ok())throw Error(await r.text());
  }
  await page.goto(url+'/');await page.getByRole('link',{name:'每日任务链 / 自动启动'}).click();
  await page.locator('#task-picker option').first().waitFor({state:'attached'});
  await page.locator('#name').fill('Daily games');await page.locator('#time').fill('06:00');await page.locator('#zone').fill('UTC');await page.locator('#enabled').uncheck();if(!(await page.locator('#resume-incomplete').isChecked()))throw Error('resume-incomplete should default on');if(await page.locator('#resume-cancelled').isChecked())throw Error('resume-cancelled should default off');
  for(const option of await page.locator('#task-picker option').all()){await page.locator('#task-picker').selectOption(await option.getAttribute('value'));await page.locator('#add-step').click()}
  await page.locator('#steps .step').nth(2).getByRole('button',{name:'上移',exact:false}).click();
  if(!(await page.locator('#steps .step').nth(1).innerText()).includes('NTE'))throw Error('reorder failed');
  await page.locator('#editor button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#chains').textContent.includes('Daily games'));
  let data=await(await page.request.get(url+'/api/chains')).json();
  if(data.chains.length!==1||data.chains[0].task_ids.join(',')!=='1,3,2'||data.chains[0].enabled||!data.chains[0].resume_incomplete_on_start||data.chains[0].resume_cancelled_on_start)throw Error('saved chain mismatch');
  await page.getByRole('button',{name:'编辑',exact:true}).click();await page.locator('#time').fill('07:30');await page.locator('#editor button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#chains').textContent.includes('07:30'));
  data=await(await page.request.get(url+'/api/chains')).json();if(data.chains.length!==1||data.chains[0].time!=='07:30')throw Error('edit did not persist');
  fs.mkdirSync('dist/ui',{recursive:true});await page.screenshot({path:'dist/ui/automation-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'dist/ui/automation-mobile.png',fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('mobile overflow');if(errors.length)throw Error(errors.join('\n'));
  console.log('PASS: dashboard navigation, task selection/reorder, save/edit persistence, desktop/mobile, no JS errors');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));fs.rmSync(root,{recursive:true,force:true})}
})().catch(e=>{console.error(e);process.exit(1)});
