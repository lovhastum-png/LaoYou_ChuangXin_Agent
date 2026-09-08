const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..');const out=path.join(root,'tmp/cross-end-qa');fs.mkdirSync(out,{recursive:true});
process.env.TEMP=path.join(root,'tmp');process.env.TMP=process.env.TEMP;
const base=process.env.LAOYOU_WEB_BASE||'http://localhost:18080';
(async()=>{
 const browser=await chromium.launch({executablePath:'D:/Chrome/Application/chrome.exe',headless:true,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required'],env:{...process.env}});
 const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['camera','microphone'],locale:'zh-CN'});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));let callId;
 const result={base,passed:false,checks:[]};
 try{
  await page.goto(base);assert.equal(await page.title(),'老友｜通通陪着您');
  await page.getByLabel('账号',{exact:true}).fill('elder');await page.getByLabel('密码',{exact:true}).fill('Laoyou123!');
  await page.getByRole('button',{name:'进入老友',exact:true}).click();await page.locator('.side-nav').waitFor();
  console.log('READY elder browser waiting for Android call');
  await page.getByRole('heading',{name:'家人视频来电',exact:true}).waitFor({timeout:240000});
  await page.getByRole('button',{name:'接听',exact:true}).click();await page.waitForURL(/\/call\//);callId=new URL(page.url()).pathname.split('/call/')[1];
  await page.getByText('通话已接通',{exact:true}).waitFor({timeout:45000});
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('video')).filter(v=>v.videoWidth>0&&v.readyState>=2&&v.srcObject).length>=2,{},{timeout:30000});
  const media=await page.locator('video').evaluateAll(vs=>vs.map(v=>({width:v.videoWidth,height:v.videoHeight,ready:v.readyState,tracks:v.srcObject?.getTracks().map(t=>({kind:t.kind,state:t.readyState}))})));
  result.media=media;result.checks.push('老人浏览器收到Android来电并接听','浏览器解码本机及安卓远端视频');
  await page.screenshot({path:path.join(out,'browser-android-connected.png'),fullPage:true});console.log('PASS browser decoded Android remote video');
  await page.getByText('通话已结束',{exact:true}).waitFor({timeout:60000});
  result.checks.push('Android挂断后浏览器同步结束');assert.deepEqual(errors,[]);result.passed=true;console.log('PASS cross-end call ended cleanly');
 }catch(e){result.error=e.message;result.errors=errors;console.error('FAIL '+e.message);process.exitCode=1;await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});}
 finally{
  if(callId){try{const login=await fetch(base+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'elder',password:'Laoyou123!'})}).then(r=>r.json());await fetch(base+'/api/calls/'+callId+'/actions',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+login.token},body:JSON.stringify({action:'end'})});}catch{}}
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));await browser.close();
 }
})();
