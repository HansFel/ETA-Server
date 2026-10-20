const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const source=fs.readFileSync(path.join(__dirname,'../include/web_ui.h'),'utf8');
const between=(start,end)=>{const a=source.indexOf(start)+start.length;return source.slice(a,source.indexOf(end,a));};
const asset={HTML:between('WEB_HTML[] PROGMEM=R"HTML(',')HTML"'),SETTINGS:between('WEB_SETTINGS[] PROGMEM=R"HTML(',')HTML"'),CSS:between('R"CSS(',')CSS"'),JS:between('R"JS(',')JS"')};
const config={name:'ETA IO Test',eta:{host:'192.168.178.22',port:8080,poll:5,stale:20},mqtt:{enabled:false,host:'',port:1883,user:'',password_set:true},dallas:{enabled:false,pin:26},i2c:{enabled:false,sda:21,scl:22,sht31:false,bme280:false,sht_address:68,bme_address:118},io:Array.from({length:8},(_,i)=>({name:'IO '+(i+1),pin:-1,mode:'disabled',inverted:false,pull:'none',seconds:60}))};
const status={device:'eta-io-test',hostname:'eta-io-test',ssid:'Heimnetz',wifi_saved:['Heimnetz'],setup_ssid:'eta-io-test-Setup',version:'1.0.0',ip:'192.168.178.100',rssi:-54,ww:'Ein',ww_permit:false,eta_ok:true,eta_age:2,mqtt:false,storage_ok:true,io:config.io.map((o,index)=>({...o,index,on:false})),sensors:[{id:'ds_2800000000000001',name:'DS18B20 2800000000000001',valid:true,temperature:56.25}]};
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1280,height:1000}});let errors=[],posted=null,wifiSet=null,wifiAdded=null,wifiRemoved=null,setupMode=false,scanCalls=0;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://eta-io.test/**',async route=>{
   const u=new URL(route.request().url()),post=route.request().method()==='POST';let body,type='application/json',code=200;
   if(u.pathname==='/'){body=asset.HTML;type='text/html';}
   else if(u.pathname==='/einstellungen'){body=asset.SETTINGS;type='text/html';}
   else if(u.pathname==='/app.js'){body=asset.JS;type='application/javascript';}
   else if(u.pathname==='/app.css'){body=asset.CSS;type='text/css';}
   else if(setupMode){code=404;type='text/plain';body='File not found\n\n';}   // WiFiManager-Portal statt Geräte-API
   else if(u.pathname==='/api/config'&&post){posted=route.request().postDataJSON();body='{"ok":true}';}
   else if(u.pathname==='/api/config')body=JSON.stringify(config);
   else if(u.pathname==='/api/status')body=JSON.stringify(status);
   else if(u.pathname==='/api/wifi-scan')body=JSON.stringify(++scanCalls<2?{running:true}:{running:false,networks:[{ssid:'Wiesenegger',rssi:-61,secure:true},{ssid:'Gast',rssi:-80,secure:false}]});
   else if(u.pathname==='/api/wifi-set'){wifiSet=route.request().postDataJSON();body='{"ok":true}';}
   else if(u.pathname==='/api/wifi-add'){const w=route.request().postDataJSON();wifiAdded=w;if(!status.wifi_saved.includes(w.ssid))status.wifi_saved.push(w.ssid);body='{"ok":true}';}
   else if(u.pathname==='/api/wifi-remove'){const w=route.request().postDataJSON();status.wifi_saved=status.wifi_saved.filter(x=>x!==w.ssid);wifiRemoved=w;body='{"ok":true}';}
   else body='{"ok":true}';
   await route.fulfill({status:code,contentType:type,body});
 });
 // Übersicht: nur Werte und Zustände, Link zu den Einstellungen, nichts zum Bedienen
 await page.goto('http://eta-io.test/');await page.waitForFunction(()=>/56,25/.test(document.querySelector('#temperatures').textContent));
 assert.equal(await page.locator('#permit').textContent(),'Gesperrt');
 assert.equal(await page.locator('form, input, select, main button').count(),0,'Übersicht darf nichts zum Bedienen enthalten');
 assert.equal(await page.locator('a.nav').getAttribute('href'),'/einstellungen');
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile overflow Übersicht');
 await page.setViewportSize({width:1280,height:1000});
 // Einstellungen: Konfiguration
 await page.goto('http://eta-io.test/einstellungen');await page.waitForFunction(()=>document.querySelectorAll('#io-config tr').length===8);
 assert.match(await page.locator('#wifi-current').textContent(),/Heimnetz/);
 await page.screenshot({path:path.join(__dirname,'../private/ui-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile overflow Einstellungen');
 await page.screenshot({path:path.join(__dirname,'../private/ui-mobile.png'),fullPage:true});
 await page.locator('#io-config tr').first().locator('input').first().fill('WW Freigabe');
 await page.locator('#io-config tr').first().locator('select').nth(0).selectOption('27');
 await page.locator('#io-config tr').first().locator('select').nth(1).selectOption('ww_permit');
 await page.locator('#dallas-enabled').check();
 await page.locator('#mqtt-password').fill('not-exported-test-secret');
 const downloadPromise=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadPromise;
 const exportText=fs.readFileSync(await download.path(),'utf8');assert(!exportText.includes('not-exported-test-secret'));assert(!('password' in JSON.parse(exportText).config.mqtt));
 await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Gespeichert'));
 assert.equal(posted.io[0].mode,'ww_permit');assert.equal(posted.io[0].pin,27);assert.equal(posted.dallas.enabled,true);
 assert.equal(posted.actions.length,6);assert.equal(posted.mqtt.tls,false);assert.equal(posted.io[0].on_value,'Ein');
 // Version 1.1: ETA-Schaltflächen, ETA-Schalter-Eingang, TLS, Rückmeldung der App
 Object.assign(config,{mqtt:{enabled:true,tls:true,host:'mqtt.example.invalid',port:8883,user:'eta-io-test',password_set:true},actions:[{label:'Wiesenegger Ein',key:'wiese-switch',value:'Ein'},{label:'Wiesenegger Aus',key:'wiese-switch',value:'Aus'},...Array.from({length:4},()=>({label:'',key:'',value:''}))]});
 config.io[1]={name:'Raumthermostat',pin:34,mode:'eta_switch',inverted:false,pull:'none',seconds:60,eta_key:'wiese-switch',on_value:'Ein',off_value:'Aus'};
 config.io[2]={name:'Pumpe',pin:14,mode:'manual',inverted:false,pull:'none',seconds:60};
 Object.assign(status,{version:'1.1.0',mqtt:true,mqtt_tls:true,mqtt_error:'',actions:[{index:0,label:'Wiesenegger Ein',key:'wiese-switch',value:'Ein'},{index:1,label:'Wiesenegger Aus',key:'wiese-switch',value:'Aus'}],points:[{key:'wiese-switch',text:'Ein',unit:'',quality:'ok',status:'confirmed',result:'Rückgelesen: Ein',result_age:4}]});
 status.io=config.io.map((o,index)=>({...o,index,on:index===1,value:index===1?'Ein':undefined,pending:false}));
 // Übersicht zeigt den ETA-Zustand und den Schalter, aber keine Knöpfe
 await page.setViewportSize({width:1280,height:1000});await page.goto('http://eta-io.test/');
 await page.waitForFunction(()=>!document.querySelector('#eta-section').hidden);
 assert.match(await page.locator('#eta-states').textContent(),/wiese-switch.*Ein.*Gesteuert über: Wiesenegger Ein, Wiesenegger Aus, Raumthermostat/);
 assert.match(await page.locator('#live-io').textContent(),/wiese-switch = Ein/);
 assert.equal(await page.locator('main button').count(),0);
 await page.screenshot({path:path.join(__dirname,'../private/ui-overview.png'),fullPage:true});
 // Einstellungen: Bedienen
 let action=null,output=null;
 await page.route('http://eta-io.test/api/eta-action',async route=>{action=route.request().postDataJSON();await route.fulfill({status:200,contentType:'application/json',body:'{"ok":true}'});});
 await page.route('http://eta-io.test/api/output',async route=>{output=route.request().postDataJSON();await route.fulfill({status:200,contentType:'application/json',body:'{"ok":true}'});});
 await page.goto('http://eta-io.test/einstellungen');
 await page.waitForFunction(()=>document.querySelectorAll('#eta-actions .card').length===2);
 assert.match(await page.locator('#eta-actions').textContent(),/Bestätigt: Rückgelesen: Ein/);
 assert.equal(await page.locator('#mqtt-tls').isChecked(),true);
 await page.locator('#eta-actions button',{hasText:'Wiesenegger Aus'}).click();await page.waitForTimeout(300);
 assert.deepEqual(action,{index:1});
 await page.locator('#manual-io button',{hasText:'Einschalten'}).click();await page.waitForTimeout(300);
 assert.deepEqual(output,{index:2,on:true});
 // WLAN: Suche, Auswahl, Speichern
 await page.locator('#wifi-scan').click();await page.waitForFunction(()=>document.querySelectorAll('#wifi-list button').length===2,null,{timeout:8000});
 await page.locator('#wifi-list button',{hasText:'Wiesenegger'}).click();
 assert.equal(await page.locator('#wifi-ssid').inputValue(),'Wiesenegger');
 await page.locator('#wifi-password').fill('geheim-12345');
 // Nur merken: Liste wächst, Verbindung bleibt
 await page.locator('#wifi-add').click();await page.waitForFunction(()=>document.querySelectorAll('#wifi-saved .card').length===2);
 assert.deepEqual(wifiAdded,{ssid:'Wiesenegger',password:'geheim-12345'});
 assert.match(await page.locator('#wifi-saved').textContent(),/Heimnetz.*Verbunden.*Wiesenegger.*Gespeichert/);
 page.once('dialog',d=>d.accept());await page.locator('#wifi-saved .card',{hasText:'Wiesenegger'}).locator('button').click();
 await page.waitForFunction(()=>document.querySelectorAll('#wifi-saved .card').length===1);assert.deepEqual(wifiRemoved,{ssid:'Wiesenegger'});
 assert.equal(await page.locator('#wifi-saved button').isDisabled(),true,'Letztes WLAN nicht entfernbar');
 await page.locator('#wifi-ssid').fill('Wiesenegger');await page.locator('#wifi-password').fill('geheim-12345');
 page.once('dialog',d=>d.accept());await page.locator('#wifi-form button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Wiesenegger'));
 assert.deepEqual(wifiSet,{ssid:'Wiesenegger',password:'geheim-12345'});
 await page.screenshot({path:path.join(__dirname,'../private/ui-desktop-1.1.png'),fullPage:true});
 // Sensornamen
 await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#sensor-names tr[data-id]').length===1);
 assert.match(await page.locator('#io-config tr').nth(1).locator('select').first().inputValue(),/^34$/);
 posted=null;await page.locator('#sensor-names tr').first().locator('input').fill('Vorlauf Süd');
 await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Gespeichert'));
 assert.deepEqual(posted.sensor_names,[{id:'ds_2800000000000001',name:'Vorlauf Süd'}]);
 assert.equal(posted.eta.enabled,true);
 assert.equal(posted.io[1].mode,'eta_switch');assert.equal(posted.io[1].eta_key,'wiese-switch');assert.equal(posted.actions[1].value,'Aus');assert.equal(posted.mqtt.tls,true);
 // Ohne direkte ETA-Abfrage: WW-Kacheln ausgeblendet
 status.eta_enabled=false;await page.goto('http://eta-io.test/');await page.waitForTimeout(800);
 assert.equal(await page.locator('#ww').isVisible(),false);assert.equal(await page.locator('#permit').isVisible(),false);assert.equal(await page.locator('#mqtt-state').isVisible(),true);
 status.eta_enabled=true;
 // Einrichtungsmodus: verständliche Meldung statt JSON-Fehler
 setupMode=true;await page.goto('http://eta-io.test/');
 await page.waitForFunction(()=>document.querySelector('#message').textContent.length>0);
 const msg=await page.locator('#message').textContent();
 assert.match(msg,/WLAN-Einrichtungsmodus/);assert.doesNotMatch(msg,/Unexpected token|JSON/);
 let polls=0;await page.route('http://eta-io.test/api/status',async r=>{polls++;await r.fulfill({status:404,contentType:'text/plain',body:'File not found'});});
 await page.waitForTimeout(7000);assert.equal(polls,0,'Im Einrichtungsmodus wird nicht weiter abgefragt');
 assert.deepEqual(errors,[]);
 console.log('PASS: overview read-only + settings page, desktop/mobile, 8 IO rows, configuration submit, secret-free export, ETA buttons/result, manual output, WiFi scan/set, sensor names, setup-mode message, no JavaScript errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
