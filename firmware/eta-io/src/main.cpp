#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WiFiManager.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <ArduinoMqttClient.h>
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_SHT31.h>
#include <Adafruit_BME280.h>
#include <esp_task_wdt.h>
#include <ArduinoOTA.h>
#include <Update.h>
#include "logic.h"
#include "provisioning.h"
#include "web_ui.h"
#include "mqtt_ca.h"

constexpr int IO_COUNT=8, SENSOR_COUNT=10, ACTION_COUNT=6, POINT_COUNT=ACTION_COUNT+IO_COUNT;
constexpr char VERSION[]="1.1.0";
struct IoConfig { String name; int pin=-1; String mode="disabled"; bool inverted=false; String pull="none"; int seconds=60; String etaKey, onValue="Ein", offValue="Aus"; };
// Schaltfläche in der Weboberfläche: sendet value an den ETA-Punkt key (über die App, nie direkt an die ETA).
struct EtaAction { String label, key, value; };
struct Config {
  String name="ETA IO", hostname, etaHost="192.168.178.22";
  int etaPort=8080, poll=5, stale=20; bool etaEnabled=true;  // direkte ETA-Abfrage (WW-Vorrang), nur im selben Netz wie die ETA
  bool mqtt=false, mqttTls=false; String mqttHost, mqttUser, mqttPassword; int mqttPort=1883;
  bool dallas=false; int oneWirePin=26;
  bool i2c=false, sht31=false, bme280=false; int sda=21,scl=22,shtAddress=68,bmeAddress=118;
  IoConfig io[IO_COUNT];
  EtaAction actions[ACTION_COUNT];
  String sensorIds[SENSOR_COUNT], sensorNames[SENSOR_COUNT];
};
struct Sensor { String id,name,topic; float temperature=NAN,humidity=NAN; uint32_t at=0; bool valid=false; };
Config cfg;
Sensor sensors[SENSOR_COUNT]; int sensorCount=0;
Preferences prefs; WebServer web(80); WiFiManager wifiManager;
WiFiClient mqttPlain; WiFiClientSecure mqttSecure; MqttClient mqtt(mqttPlain);
// Verbindungsaufbau (DNS, TLS) läuft in einer eigenen Task, damit die Ausgangslogik nie blockiert.
volatile bool mqttConnecting=false, mqttConnectDone=false; volatile int mqttConnectResult=0; String mqttError="Nicht konfiguriert";
// Zuletzt bekannter Zustand und letzte Rückmeldung je ETA-Punkt (aus eta/dev/<benutzer>/state und /result).
struct PointState { String key, text, unit, quality, status, result, pendingId; uint32_t at=0, resultAt=0; };
PointState points[POINT_COUNT]; int pointCount=0;
Debounce switches[IO_COUNT]; bool switchPending[IO_COUNT]={};
OneWire* oneWire=nullptr; DallasTemperature* dallas=nullptr;
DeviceAddress addresses[8]; int dallasCount=0;
// 1-Wire-Diagnose beim Start: Antwortet überhaupt ein Gerät (Presence), wie viele Geräte, wie viele davon keine DS18B20.
bool oneWirePresence=false; int oneWireTotal=0, oneWireOther=0;
Adafruit_SHT31 sht; Adafruit_BME280 bme; bool shtReady=false,bmeReady=false;
String deviceId,baseTopic,topicId,adminPassword;
String hostName() { return cfg.hostname.length()?cfg.hostname:deviceId; }
bool outputs[IO_COUNT]={},requested[IO_COUNT]={}; uint32_t requestedAt[IO_COUNT]={};
WwState ww=WwState::Unknown; bool etaValid=false; uint32_t etaAt=0,lastPoll=0,lastMqtt=0,lastPublish=0,lastSensors=0,conversionAt=0;
bool converting=false,webStarted=false,discoveryNeeded=true,portalRequested=false,storageOk=true;
bool updating=false,uploadAllowed=false,uploadFailed=false,otaStarted=false;
bool portalActive=false;
uint32_t rebootAt=0; String etaError="Noch keine Antwort",serialLine;
// WLAN nach dem Start: Wird das gespeicherte Netz nicht innerhalb von WIFI_BOOT_WAIT gefunden, öffnet das Setup-WLAN.
// Ein späterer Verbindungsverlust im Betrieb öffnet es nicht (nur Neuverbindung).
constexpr uint32_t WIFI_BOOT_WAIT=60000; uint32_t staSince=0; bool connectedSinceBoot=false, eraseWifi=false;
// Mehrere gespeicherte WLANs (z. B. Heimnetz und Wiesenegger). Verbunden ist immer nur eines; gewählt wird das
// stärkste vorhandene. Passwörter bleiben im Gerät (eigener NVS-Eintrag, nie in Status oder Export).
constexpr int WIFI_MAX=3; struct WifiNet { String ssid, pass; }; WifiNet wifis[WIFI_MAX]; int wifiCount=0;
bool wifiAttempting=false, wifiRemembered=false; uint32_t wifiAttemptAt=0, wifiLostAt=0, wifiLastScan=0; int wifiLastTried=-1;
String newSsid, newWifiPassword;  // neues WLAN aus den Einstellungen, wird vor dem Neustart gespeichert

String wwText() { return ww==WwState::On?"Ein":ww==WwState::Off?"Aus":"Unbekannt"; }
bool safeHost(const String& value) {
  if (!value.length()||value.length()>120) return false;
  for (char c:value) if (!isalnum(static_cast<unsigned char>(c)) && c!='.' && c!='-') return false;
  return true;
}
void toJson(const Config& c, JsonDocument& doc, bool secrets=false) {
  doc["name"]=c.name; doc["hostname"]=c.hostname; auto eta=doc["eta"].to<JsonObject>();
  eta["enabled"]=c.etaEnabled;eta["host"]=c.etaHost;eta["port"]=c.etaPort;eta["poll"]=c.poll;eta["stale"]=c.stale;
  auto m=doc["mqtt"].to<JsonObject>();m["enabled"]=c.mqtt;m["tls"]=c.mqttTls;m["host"]=c.mqttHost;m["port"]=c.mqttPort;m["user"]=c.mqttUser;
  if (secrets) m["password"]=c.mqttPassword; else m["password_set"]=c.mqttPassword.length()>0;
  auto d=doc["dallas"].to<JsonObject>();d["enabled"]=c.dallas;d["pin"]=c.oneWirePin;
  auto i=doc["i2c"].to<JsonObject>();i["enabled"]=c.i2c;i["sda"]=c.sda;i["scl"]=c.scl;i["sht31"]=c.sht31;i["bme280"]=c.bme280;i["sht_address"]=c.shtAddress;i["bme_address"]=c.bmeAddress;
  auto array=doc["io"].to<JsonArray>();
  for (int n=0;n<IO_COUNT;n++) { auto o=array.add<JsonObject>();o["name"]=c.io[n].name;o["pin"]=c.io[n].pin;o["mode"]=c.io[n].mode;o["inverted"]=c.io[n].inverted;o["pull"]=c.io[n].pull;o["seconds"]=c.io[n].seconds;o["eta_key"]=c.io[n].etaKey;o["on_value"]=c.io[n].onValue;o["off_value"]=c.io[n].offValue; }
  auto actions=doc["actions"].to<JsonArray>();
  for (int n=0;n<ACTION_COUNT;n++) { auto o=actions.add<JsonObject>();o["label"]=c.actions[n].label;o["key"]=c.actions[n].key;o["value"]=c.actions[n].value; }
  auto names=doc["sensor_names"].to<JsonArray>();
  for (int n=0;n<SENSOR_COUNT;n++) if (c.sensorIds[n].length()) { auto o=names.add<JsonObject>();o["id"]=c.sensorIds[n];o["name"]=c.sensorNames[n]; }
}
String validate(JsonDocument& doc, Config& result) {
  if (!doc["name"].is<String>()||!doc["eta"].is<JsonObject>()||!doc["mqtt"].is<JsonObject>()||!doc["io"].is<JsonArray>()) return "Konfiguration unvollstaendig";
  result.name=doc["name"].as<String>(); if (!result.name.length()||result.name.length()>48) return "Name: 1 bis 48 Zeichen";
  result.hostname=doc["hostname"]|""; result.hostname.trim();
  if (result.hostname.length()&&!validHostname(result.hostname.c_str())) return "Hostname: 1 bis 32 Zeichen, nur a-z, 0-9 und Bindestrich (kein Unterstrich)";
  auto eta=doc["eta"].as<JsonObject>();result.etaEnabled=eta["enabled"]|true;result.etaHost=eta["host"].as<String>();result.etaPort=eta["port"]|0;result.poll=eta["poll"]|0;result.stale=eta["stale"]|0;
  if (!safeHost(result.etaHost)||result.etaPort<1||result.etaPort>65535||result.poll<2||result.poll>60||result.stale<result.poll+3||result.stale>300) return "ETA: Host/Port, Intervall 2..60 s, Frist mindestens Intervall + 3 s, maximal 300 s";
  auto m=doc["mqtt"].as<JsonObject>();result.mqtt=m["enabled"]|false;result.mqttHost=m["host"]|"";result.mqttPort=m["port"]|1883;result.mqttUser=m["user"]|"";result.mqttTls=m["tls"]|false;
  if (!m["password"].isNull()) result.mqttPassword=m["password"].as<String>();
  if (result.mqtt&&( !safeHost(result.mqttHost)||result.mqttPort<1||result.mqttPort>65535)) return "MQTT-Host oder Port ungueltig";
  if (result.mqttUser.length()>128||result.mqttPassword.length()>128) return "MQTT-Zugangsdaten zu lang";
  // Der Benutzer wird Teil der Topics (etaio/<benutzer>, eta/dev/<benutzer>): nur einfache Zeichen.
  for (char ch:result.mqttUser) if (!((ch>='a'&&ch<='z')||(ch>='0'&&ch<='9')||ch=='-'||ch=='_')) return "MQTT-Benutzer: nur a-z, 0-9, - und _";
  result.dallas=doc["dallas"]["enabled"]|false;result.oneWirePin=doc["dallas"]["pin"]|26;
  auto i=doc["i2c"].as<JsonObject>();result.i2c=i["enabled"]|false;result.sda=i["sda"]|21;result.scl=i["scl"]|22;result.sht31=i["sht31"]|false;result.bme280=i["bme280"]|false;result.shtAddress=i["sht_address"]|68;result.bmeAddress=i["bme_address"]|118;
  bool occupied[40]={};
  auto reserve=[&](int p){ if (!outputPin(p)||occupied[p]) return false; occupied[p]=true;return true; };
  if (result.dallas&&!reserve(result.oneWirePin)) return "Ungueltiger oder doppelt belegter 1-Wire-GPIO";
  if (result.i2c&&(!reserve(result.sda)||!reserve(result.scl))) return "Ungueltige oder doppelte I2C-Pins";
  if ((result.shtAddress!=68&&result.shtAddress!=69)||(result.bmeAddress!=118&&result.bmeAddress!=119)) return "Ungueltige Sensoradresse";
  auto a=doc["io"].as<JsonArray>();if (a.size()!=IO_COUNT) return "Genau 8 IO-Plaetze erforderlich";
  for (int n=0;n<IO_COUNT;n++) {
    auto o=a[n].as<JsonObject>();auto& out=result.io[n];out.name=o["name"]|"";out.pin=o["pin"]|-1;out.mode=o["mode"]|"disabled";out.inverted=o["inverted"]|false;out.pull=o["pull"]|"none";out.seconds=o["seconds"]|60;out.etaKey=o["eta_key"]|"";out.onValue=o["on_value"]|"Ein";out.offValue=o["off_value"]|"Aus";
    if (out.name.length()>48||out.seconds<1||out.seconds>3600) return "IO-Name maximal 48 Zeichen; Einschaltdauer 1..3600 s";
    if (out.mode!="disabled"&&out.mode!="input"&&out.mode!="manual"&&out.mode!="ww_permit"&&out.mode!="eta_switch") return "Unbekannte IO-Funktion";
    if (out.mode=="ww_permit"&&!result.etaEnabled) return "WW-Freigabe braucht die direkte ETA-Abfrage (Gerät & ETA)";
    if (out.mode=="eta_switch"&&(!validPointKey(out.etaKey.c_str())||!validPointValue(out.onValue.c_str())||!validPointValue(out.offValue.c_str()))) return "ETA-Schalter: ETA-Punkt (z. B. wiese-switch) sowie Werte fuer EIN und AUS angeben";
    if (out.pull!="none"&&out.pull!="up"&&out.pull!="down") return "Unbekannter Eingangswiderstand";
    if (out.mode=="disabled") continue;
    if (!((out.mode=="input"||out.mode=="eta_switch")?inputPin(out.pin):outputPin(out.pin))) return "GPIO fuer diese Funktion nicht freigegeben";
    if (occupied[out.pin]) return "GPIO mehrfach belegt";
    occupied[out.pin]=true;
    if ((out.mode=="input"||out.mode=="eta_switch")&&out.pin>=34&&out.pull!="none") return "GPIO 34..39 haben keine internen Pull-Widerstaende";
  }
  auto actions=doc["actions"].as<JsonArray>();
  for (int n=0;n<ACTION_COUNT;n++) {
    auto& out=result.actions[n];JsonObject o=actions.isNull()||n>=(int)actions.size()?JsonObject():actions[n].as<JsonObject>();
    out.label=o["label"]|"";out.key=o["key"]|"";out.value=o["value"]|"";
    if (!out.label.length()&&!out.key.length()&&!out.value.length()) continue;
    if (out.label.length()<1||out.label.length()>32||!validPointKey(out.key.c_str())||!validPointValue(out.value.c_str())) return "Schaltflaeche "+String(n+1)+": Beschriftung (max. 32), ETA-Punkt und Wert angeben";
  }
  if (result.mqtt&&!result.mqttUser.length()) for (int n=0;n<IO_COUNT;n++) if (result.io[n].mode=="eta_switch") return "ETA-Schalter brauchen einen MQTT-Benutzer";
  for (int n=0;n<SENSOR_COUNT;n++) {result.sensorIds[n]="";result.sensorNames[n]="";}
  auto names=doc["sensor_names"].as<JsonArray>();int count=0;
  if (!names.isNull()) for (JsonObject o:names) {
    String id=o["id"]|"", name=o["name"]|"";name.trim();
    if (!name.length()) continue;
    bool ds=id.length()==19&&id.startsWith("ds_");for (int i=3;ds&&i<19;i++) ds=isxdigit(static_cast<unsigned char>(id[i]))&&!isupper(static_cast<unsigned char>(id[i]));
    if (!(ds||id=="sht31"||id=="bme280")) return "Unbekannter Sensor: "+id;
    if (name.length()>32||!validPointValue(name.c_str())) return "Sensorname: 1 bis 32 Zeichen";
    if (count>=SENSOR_COUNT) return "Zu viele Sensornamen";
    result.sensorIds[count]=id;result.sensorNames[count++]=name;
  }
  return "";
}
String configuredName(const String& id) { for (int n=0;n<SENSOR_COUNT;n++) if (cfg.sensorIds[n]==id) return cfg.sensorNames[n]; return ""; }
// Topic-Teil aus dem Namen; bei leerem Ergebnis, reservierten Wörtern oder Doppelung bleibt die Sensor-ID.
void assignSensorTopics() {
  const char* reserved[]={"ww","eta","permit","availability","sensors","io0","io1","io2","io3","io4","io5","io6","io7"};
  for (int n=0;n<sensorCount;n++) {
    String name=configuredName(sensors[n].id);if (name.length()) sensors[n].name=name;
    char slug[33];topicSlug(name.c_str(),slug,sizeof slug);String topic=slug;
    for (auto r:reserved) if (topic==r) topic="";
    for (int m=0;m<n;m++) if (sensors[m].topic==topic) topic="";
    sensors[n].topic=topic.length()?topic:sensors[n].id;
  }
}
bool isOutput(int i) { return cfg.io[i].mode=="manual"||cfg.io[i].mode=="ww_permit"; }
void setOutput(int i,bool value) {
  if (!isOutput(i)) return;
  outputs[i]=value;digitalWrite(cfg.io[i].pin,(value != cfg.io[i].inverted)?HIGH:LOW);
}
void allOff() { for (int i=0;i<IO_COUNT;i++) {requested[i]=false;setOutput(i,false);} }
void applyControl() {
  if(updating||rebootAt||!storageOk||portalActive){allOff();return;}
  uint32_t now=millis();bool fresh=etaValid&&WiFi.status()==WL_CONNECTED&&uint32_t(now-etaAt)<uint32_t(cfg.stale)*1000;
  for (int i=0;i<IO_COUNT;i++) {
    if (cfg.io[i].mode=="ww_permit") setOutput(i,wwPermit(ww,fresh,now,etaAt,cfg.stale*1000));
    if (cfg.io[i].mode=="manual") {
      bool on=WiFi.status()==WL_CONNECTED && manualPermit(requested[i],now,requestedAt[i],cfg.io[i].seconds*1000);
      if (!on) requested[i]=false;
      setOutput(i,on);
    }
  }
}
void setupPins() {
  for (int n=0;n<IO_COUNT;n++) {
    auto& o=cfg.io[n];
    if (isOutput(n)) {digitalWrite(o.pin,o.inverted?HIGH:LOW);pinMode(o.pin,OUTPUT);setOutput(n,false);}
    else if (o.mode=="input"||o.mode=="eta_switch") pinMode(o.pin,o.pull=="up"?INPUT_PULLUP:o.pull=="down"?INPUT_PULLDOWN:INPUT);
  }
}
String addressText(const uint8_t* address) { char out[17];for(int n=0;n<8;n++)snprintf(out+n*2,3,"%02x",address[n]);return out; }
void setupSensors() {
  if (cfg.dallas) {
    oneWire=new OneWire(cfg.oneWirePin);oneWirePresence=oneWire->reset();dallas=new DallasTemperature(oneWire);dallas->begin();dallas->setWaitForConversion(false);
    oneWireTotal=dallas->getDeviceCount();
    for (int n=0;n<oneWireTotal&&dallasCount<8;n++) {
      DeviceAddress a;if (!dallas->getAddress(a,n)||a[0]!=0x28) {oneWireOther++;continue;}
      memcpy(addresses[dallasCount],a,8);dallas->setResolution(a,12);
      sensors[sensorCount].id="ds_"+addressText(a);sensors[sensorCount].name="DS18B20 "+addressText(a);sensorCount++;dallasCount++;
    }
  }
  if (cfg.i2c) {
    Wire.begin(cfg.sda,cfg.scl);Wire.setTimeOut(50);
    if (cfg.sht31) {shtReady=sht.begin(cfg.shtAddress);sensors[sensorCount].id="sht31";sensors[sensorCount++].name="SHT31";}
    if (cfg.bme280) {bmeReady=bme.begin(cfg.bmeAddress);sensors[sensorCount].id="bme280";sensors[sensorCount++].name="BME280";}
  }
  assignSensorTopics();
}
void updateSensor(int index,float t,float humidity=NAN) {
  auto& s=sensors[index];s.valid=isfinite(t)&&t>=-55&&t<=125;s.temperature=s.valid?t:NAN;s.humidity=humidity;s.at=millis();
}
void readSensors() {
  uint32_t now=millis();
  if (converting&&uint32_t(now-conversionAt)>=800) {
    for(int n=0;n<dallasCount;n++) {float t=dallas->getTempC(addresses[n]);updateSensor(n,t==DEVICE_DISCONNECTED_C?NAN:t);}
    converting=false;
  }
  if (uint32_t(now-lastSensors)<5000) return;lastSensors=now;
  if(dallas){dallas->requestTemperatures();conversionAt=now;converting=true;}
  for(int n=dallasCount;n<sensorCount;n++) {
    if(sensors[n].id=="sht31") {if(!shtReady)shtReady=sht.begin(cfg.shtAddress);updateSensor(n,shtReady?sht.readTemperature():NAN,shtReady?sht.readHumidity():NAN);}
    if(sensors[n].id=="bme280") {if(!bmeReady)bmeReady=bme.begin(cfg.bmeAddress);updateSensor(n,bmeReady?bme.readTemperature():NAN,bmeReady?bme.readHumidity():NAN);}
  }
}
void pollEta() {
  if(!cfg.etaEnabled){etaValid=false;ww=WwState::Unknown;etaError="Direkte ETA-Abfrage ausgeschaltet";return;}
  if(uint32_t(millis()-lastPoll)<uint32_t(cfg.poll)*1000)return;lastPoll=millis();
  if(WiFi.status()!=WL_CONNECTED){etaValid=false;ww=WwState::Unknown;etaError="WLAN getrennt";return;}
  WiFiClient transport; HTTPClient http;
  String url="http://"+cfg.etaHost+":"+cfg.etaPort+"/user/var/121/10111/0/0/12080";
  http.setConnectTimeout(1000);http.setTimeout(1000);http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  WwState next=WwState::Unknown;int code=-1;
  if(http.begin(transport,url)) {
    code=http.GET();
    if(code==200&&http.getSize()>0&&http.getSize()<=4096) {String xml=http.getString();if(xml.length()<=4096)next=parseWw(xml.c_str());}
    http.end();
  }
  ww=next;etaValid=next!=WwState::Unknown;
  if(etaValid){etaAt=millis();etaError="";}else etaError="Keine gueltige WW-Antwort (HTTP "+String(code)+")";
}
String resetReasonText() {
  switch(esp_reset_reason()){case ESP_RST_POWERON:return "Einschalten";case ESP_RST_SW:return "Neustart durch Firmware";case ESP_RST_PANIC:return "Absturz (Panic)";
    case ESP_RST_INT_WDT:case ESP_RST_TASK_WDT:case ESP_RST_WDT:return "Watchdog";case ESP_RST_BROWNOUT:return "Unterspannung";case ESP_RST_DEEPSLEEP:return "Deep Sleep";default:return "Sonstiges";}
}
bool isInputMode(int n) { return cfg.io[n].mode=="input"||cfg.io[n].mode=="eta_switch"; }
bool inputState(int n) { return cfg.io[n].mode=="eta_switch"?switches[n].stable:(bool(digitalRead(cfg.io[n].pin))!=cfg.io[n].inverted); }
bool mqttUp() { return !mqttConnecting&&mqtt.connected(); }
PointState* pointState(const String& key) { for (int n=0;n<pointCount;n++) if (points[n].key==key) return &points[n]; return nullptr; }
void trackPoint(const String& key) { if (key.length()&&!pointState(key)&&pointCount<POINT_COUNT) points[pointCount++].key=key; }
String newUuid() {
  uint8_t b[16];esp_fill_random(b,16);b[6]=(b[6]&0x0f)|0x40;b[8]=(b[8]&0x3f)|0x80;char out[37];
  snprintf(out,sizeof out,"%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9],b[10],b[11],b[12],b[13],b[14],b[15]);
  return out;
}
// Schaltbefehl an die App (QoS 1). Die App prüft Rechte, Wertebereich und schreibt erst dann in die ETA.
bool sendEta(const String& key,const String& value) {
  auto* p=pointState(key);
  if (!mqttUp()||!cfg.mqttUser.length()) { if(p){p->status="nicht gesendet";p->result="MQTT nicht verbunden";p->resultAt=millis();} return false; }
  JsonDocument d;String id=newUuid();d["command_id"]=id;d["value"]=value;String out;serializeJson(d,out);
  String topic="eta/dev/"+cfg.mqttUser+"/cmd/"+key+"/set";
  mqtt.beginMessage(topic,(unsigned long)out.length(),false,1);mqtt.print(out);bool ok=mqtt.endMessage()==1;
  if (p) { p->pendingId=ok?id:"";p->status=ok?"gesendet":"nicht gesendet";p->result=ok?"Warte auf Rueckmeldung der App":"Senden fehlgeschlagen";p->resultAt=millis(); }
  return ok;
}
void handleEtaSwitches() {
  uint32_t now=millis();
  for (int n=0;n<IO_COUNT;n++) {
    if (cfg.io[n].mode!="eta_switch") continue;
    if (debounceStep(switches[n],bool(digitalRead(cfg.io[n].pin))!=cfg.io[n].inverted,now,1000)) switchPending[n]=true;
    // Nur Änderungen senden; nach einem Verbindungsabbruch wird der aktuelle Zustand nachgereicht.
    if (switchPending[n]&&mqttUp()&&sendEta(cfg.io[n].etaKey,switches[n].stable?cfg.io[n].onValue:cfg.io[n].offValue)) {switchPending[n]=false;lastPublish=0;}
  }
}
void jsonResponse(int code,JsonDocument& doc) {String out;serializeJson(doc,out);web.send(code,"application/json; charset=utf-8",out);}
void errorResponse(int code,const String& message) {JsonDocument d;d["error"]=message;jsonResponse(code,d);}
bool authorized(bool mutation=false) {
  web.sendHeader("Cache-Control","no-store");web.sendHeader("X-Content-Type-Options","nosniff");web.sendHeader("X-Frame-Options","DENY");
  if(!web.authenticate("admin",adminPassword.c_str())) {web.requestAuthentication(DIGEST_AUTH,"ETA-IO","Anmeldung erforderlich");return false;}
  // Custom header and same-origin fetch protect HTTP Digest requests from CSRF.
  if(mutation&&(web.header("X-ETA-Action")!="configure"||web.header("Content-Type")!="application/json")) {errorResponse(403,"Ungueltige Anfrage");return false;}
  return true;
}
bool readBody(JsonDocument& d) {
  if(web.arg("plain").length()>8192){errorResponse(413,"Anfrage zu gross");return false;}
  if(deserializeJson(d,web.arg("plain"))){errorResponse(400,"Ungueltiges JSON");return false;}return true;
}
void statusJson(JsonDocument& d) {
  uint32_t now=millis();bool fresh=etaValid&&WiFi.status()==WL_CONNECTED&&uint32_t(now-etaAt)<uint32_t(cfg.stale)*1000;
  d["device"]=deviceId;d["hostname"]=hostName();d["reset_reason"]=resetReasonText();d["eta_enabled"]=cfg.etaEnabled;d["ssid"]=WiFi.SSID();{auto w=d["wifi_saved"].to<JsonArray>();for(int n=0;n<wifiCount;n++)w.add(wifis[n].ssid);}d["version"]=VERSION;d["ip"]=WiFi.localIP().toString();d["rssi"]=WiFi.RSSI();d["uptime"]=now/1000;d["mqtt"]=mqttUp();d["mqtt_error"]=mqttUp()?"":mqttError;d["mqtt_topic"]=baseTopic;d["mqtt_tls"]=cfg.mqttTls;d["ww"]=fresh?wwText():"Unbekannt";d["eta_ok"]=fresh;d["eta_error"]=etaError;d["eta_age"]=etaAt?uint32_t(now-etaAt)/1000:0;
  d["ww_permit"]=!updating&&!portalActive&&storageOk&&wwPermit(ww,fresh,now,etaAt,cfg.stale*1000);d["storage_ok"]=storageOk;
  d["setup_active"]=portalActive;d["setup_ssid"]=deviceId+"-Setup";
  auto a=d["io"].to<JsonArray>();
  for(int n=0;n<IO_COUNT;n++){auto o=a.add<JsonObject>();o["index"]=n;o["name"]=cfg.io[n].name;o["mode"]=cfg.io[n].mode;o["pin"]=cfg.io[n].pin;o["on"]=isInputMode(n)?inputState(n):outputs[n];if(cfg.io[n].mode=="eta_switch"){o["eta_key"]=cfg.io[n].etaKey;o["value"]=switches[n].stable?cfg.io[n].onValue:cfg.io[n].offValue;o["pending"]=switchPending[n];}}
  auto act=d["actions"].to<JsonArray>();
  for(int n=0;n<ACTION_COUNT;n++){if(!cfg.actions[n].label.length())continue;auto o=act.add<JsonObject>();o["index"]=n;o["label"]=cfg.actions[n].label;o["key"]=cfg.actions[n].key;o["value"]=cfg.actions[n].value;}
  auto pts=d["points"].to<JsonArray>();
  for(int n=0;n<pointCount;n++){auto o=pts.add<JsonObject>();auto& p=points[n];o["key"]=p.key;bool fresh=p.at&&uint32_t(now-p.at)<180000&&p.quality=="ok";o["text"]=fresh?p.text:"";o["unit"]=p.unit;o["quality"]=p.at?(fresh?p.quality:String("stale")):String("unbekannt");o["status"]=p.status;o["result"]=p.result;o["result_age"]=p.resultAt?uint32_t(now-p.resultAt)/1000:0;}
  if(cfg.dallas){auto w=d["onewire"].to<JsonObject>();w["pin"]=cfg.oneWirePin;w["presence"]=oneWirePresence;w["devices"]=oneWireTotal;w["ds18b20"]=dallasCount;w["other"]=oneWireOther;}
  auto s=d["sensors"].to<JsonArray>();
  for(int n=0;n<sensorCount;n++){auto o=s.add<JsonObject>();o["id"]=sensors[n].id;o["name"]=sensors[n].name;o["topic"]=baseTopic+"/"+sensors[n].topic;bool valid=sensors[n].valid&&uint32_t(now-sensors[n].at)<15000;o["valid"]=valid;if(valid)o["temperature"]=sensors[n].temperature;else o["temperature"]=nullptr;if(valid&&isfinite(sensors[n].humidity))o["humidity"]=sensors[n].humidity;}
}
bool mqttPublish(const String& topic,const String& payload,bool retained=false) {
  if(!mqttUp())return false;
  mqtt.beginMessage(topic.c_str(),(unsigned long)payload.length(),retained,0);
  // Kein Schreiben mit 0 Byte: über TLS hängt die ESP32-Bibliothek dabei endlos (Watchdog-Neustart).
  if(payload.length())mqtt.print(payload);
  return mqtt.endMessage()==1;
}
void loadWifis() {
  wifiCount=0;JsonDocument d;
  if(deserializeJson(d,prefs.getString("wifis","[]")))return;
  for(JsonObject o:d.as<JsonArray>()){if(wifiCount>=WIFI_MAX)break;String ss=o["ssid"]|"";if(!ss.length())continue;wifis[wifiCount].ssid=ss;wifis[wifiCount++].pass=o["pass"]|"";}
}
bool saveWifis() {
  JsonDocument d;auto a=d.to<JsonArray>();
  for(int n=0;n<wifiCount;n++){auto o=a.add<JsonObject>();o["ssid"]=wifis[n].ssid;o["pass"]=wifis[n].pass;}
  String out;serializeJson(d,out);return prefs.putString("wifis",out)==out.length();
}
int wifiIndex(const String& ssid){for(int n=0;n<wifiCount;n++)if(wifis[n].ssid==ssid)return n;return -1;}
// Neues WLAN merken bzw. Passwort aktualisieren; bei voller Liste wird das älteste (erste) ersetzt.
bool rememberWifi(const String& ssid,const String& pass) {
  int n=wifiIndex(ssid);
  if(n<0){if(wifiCount>=WIFI_MAX){for(int i=1;i<WIFI_MAX;i++)wifis[i-1]=wifis[i];wifiCount--;}n=wifiCount++;}
  else if(wifis[n].pass==pass)return true;
  wifis[n].ssid=ssid;wifis[n].pass=pass;return saveWifis();
}
bool validWifi(const String& ssid,const String& pw){return ssid.length()>=1&&ssid.length()<=32&&pw.length()<=63&&(!pw.length()||pw.length()>=8);}
// Nicht blockierend: zuerst das zuletzt benutzte WLAN (setup), danach Suche über alle gespeicherten Netze.
void manageWifi() {
  uint32_t now=millis();
  if(WiFi.status()==WL_CONNECTED){
    wifiLostAt=0;wifiAttempting=false;
    // Auch über das Setup-WLAN oder früher eingerichtete Netze landen so in der Liste.
    if(!wifiRemembered){wifiRemembered=true;if(WiFi.SSID().length())rememberWifi(WiFi.SSID(),WiFi.psk());}
    return;
  }
  wifiRemembered=false;
  if(!wifiLostAt)wifiLostAt=now;
  if(wifiAttempting&&uint32_t(now-wifiAttemptAt)<15000)return;
  if(wifiAttempting){wifiAttempting=false;}
  // Im Betrieb erst die automatische Neuverbindung zum selben Netz abwarten.
  if(connectedSinceBoot&&uint32_t(now-wifiLostAt)<60000)return;
  if(!wifiCount)return;
  int n=WiFi.scanComplete();
  if(n==WIFI_SCAN_FAILED){if(uint32_t(now-wifiLastScan)>=10000){wifiLastScan=now;WiFi.scanNetworks(true,false);}return;}
  if(n==WIFI_SCAN_RUNNING)return;
  int best=-1,bestRssi=-1000,alt=-1,altRssi=-1000;
  for(int i=0;i<n;i++){int k=wifiIndex(WiFi.SSID(i));if(k<0)continue;int r=WiFi.RSSI(i);
    if(k!=wifiLastTried){if(r>bestRssi){best=k;bestRssi=r;}}else if(r>altRssi){alt=k;altRssi=r;}}
  WiFi.scanDelete();
  if(best<0)best=alt;  // nur das zuletzt erfolglose Netz ist da: erneut versuchen
  if(best<0)return;    // kein gespeichertes Netz in Reichweite: nächste Suche (nach dem Start öffnet dann das Setup-WLAN)
  Serial.println("Verbinde mit gespeichertem WLAN: "+wifis[best].ssid);
  WiFi.begin(wifis[best].ssid.c_str(),wifis[best].pass.length()?wifis[best].pass.c_str():nullptr);
  wifiAttempting=true;wifiAttemptAt=now;wifiLastTried=best;
}
void publishDiscovery();
void startPortal() {
  allOff();etaValid=false;
  if(mqttUp()){mqttPublish(baseTopic+"/availability","offline",true);mqtt.stop();}
  if(webStarted){web.stop();webStarted=false;}
  Serial.println("WLAN-Einrichtung: "+deviceId+"-Setup, Adresse 192.168.4.1. Passwort in privater Zugangskarte.");
  wifiManager.setDebugOutput(false);wifiManager.setConfigPortalTimeout(300);wifiManager.setConnectTimeout(5);wifiManager.setBreakAfterConfig(true);wifiManager.setConfigPortalBlocking(false);
  portalActive=true;
  wifiManager.startConfigPortal((deviceId+"-Setup").c_str(),SETUP_PASSWORD);
}
void setupWeb() {
  const char* headers[]={"X-ETA-Action","Content-Type"};web.collectHeaders(headers,2);
  web.on("/",HTTP_GET,[]{if(!authorized())return;web.sendHeader("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'");web.send_P(200,"text/html; charset=utf-8",WEB_HTML);});
  web.on("/einstellungen",HTTP_GET,[]{if(!authorized())return;web.sendHeader("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'");web.send_P(200,"text/html; charset=utf-8",WEB_SETTINGS);});
  web.on("/app.js",HTTP_GET,[]{if(authorized())web.send_P(200,"application/javascript; charset=utf-8",WEB_JS);});
  web.on("/app.css",HTTP_GET,[]{if(authorized())web.send_P(200,"text/css",WEB_CSS);});
  web.on("/api/status",HTTP_GET,[]{if(!authorized())return;JsonDocument d;statusJson(d);jsonResponse(200,d);});
  web.on("/api/config",HTTP_GET,[]{if(!authorized())return;JsonDocument d;toJson(cfg,d);jsonResponse(200,d);});
  web.on("/api/config",HTTP_POST,[]{
    if(!authorized(true))return;JsonDocument d;if(!readBody(d))return;
    Config candidate=cfg;String issue=validate(d,candidate);if(issue.length()){errorResponse(400,issue);return;}
    JsonDocument stored;toJson(candidate,stored,true);String text;serializeJson(stored,text);
    allOff();
    if(prefs.putString("config",text)!=text.length()){storageOk=false;errorResponse(500,"Speichern fehlgeschlagen");return;}
    // Remove retained old entities on the old broker before switching configuration.
    if(mqttUp()) {
      for(int n=0;n<IO_COUNT;n++)for(const char* type:{"switch","binary_sensor"})mqttPublish("homeassistant/"+String(type)+"/"+topicId+"/io"+n+"/config","",true);
      for(int n=0;n<sensorCount;n++)for(const char* suffix:{"_temperature","_humidity"})mqttPublish("homeassistant/sensor/"+topicId+"/"+sensors[n].id+suffix+"/config","",true);
      mqttPublish(baseTopic+"/availability","offline",true);
    }
    web.send(200,"application/json","{\"ok\":true,\"restart\":true}");rebootAt=millis()+1000;
  });
  web.on("/api/output",HTTP_POST,[]{
    if(!authorized(true))return;JsonDocument d;if(!readBody(d))return;
    int n=d["index"]|-1;if(n<0||n>=IO_COUNT||cfg.io[n].mode!="manual"||!d["on"].is<bool>()){errorResponse(400,"Nur manuelle Ausgaenge schaltbar");return;}
    requested[n]=d["on"].as<bool>();requestedAt[n]=millis();applyControl();web.send(200,"application/json","{\"ok\":true}");
  });
  web.on("/api/eta-action",HTTP_POST,[]{
    if(!authorized(true))return;JsonDocument d;if(!readBody(d))return;
    int n=d["index"]|-1;if(n<0||n>=ACTION_COUNT||!cfg.actions[n].label.length()){errorResponse(400,"Unbekannte Schaltflaeche");return;}
    if(!sendEta(cfg.actions[n].key,cfg.actions[n].value)){errorResponse(503,"MQTT nicht verbunden: "+mqttError);return;}
    web.send(200,"application/json","{\"ok\":true}");
  });
  web.on("/api/password",HTTP_POST,[]{if(!authorized(true))return;JsonDocument d;if(!readBody(d))return;String password=d["password"]|"";if(password.length()<12||password.length()>100){errorResponse(400,"Passwort: 12 bis 100 Zeichen");return;}if(prefs.putString("password",password)!=password.length()){errorResponse(500,"Speichern fehlgeschlagen");return;}adminPassword=password;ArduinoOTA.setPassword(adminPassword.c_str());web.send(200,"application/json","{\"ok\":true}");});
  web.on("/api/firmware",HTTP_POST,[]{
    if(!authorized())return;
    if(!uploadAllowed||uploadFailed||Update.hasError()){errorResponse(400,"Firmware abgelehnt oder Upload abgebrochen. Ausgaenge bleiben bis Neustart aus.");return;}
    web.send(200,"application/json","{\"ok\":true}");rebootAt=millis()+1000;
  },[]{
    HTTPUpload& upload=web.upload();
    if(upload.status==UPLOAD_FILE_START){
      uploadAllowed=web.authenticate("admin",adminPassword.c_str())&&web.header("X-ETA-Action")=="configure";
      uploadFailed=false;if(!uploadAllowed)return;
      updating=true;allOff();uploadFailed=!Update.begin(UPDATE_SIZE_UNKNOWN,U_FLASH);
      if(mqttUp()){mqttPublish(baseTopic+"/availability","offline",true);mqtt.stop();}
    }else if(uploadAllowed&&upload.status==UPLOAD_FILE_WRITE){
      allOff();esp_task_wdt_reset();
      if(!uploadFailed&&Update.write(upload.buf,upload.currentSize)!=upload.currentSize)uploadFailed=true;
    }else if(uploadAllowed&&upload.status==UPLOAD_FILE_END){
      if(uploadFailed||!Update.end(true)){uploadFailed=true;Update.abort();}
    }else if(uploadAllowed&&upload.status==UPLOAD_FILE_ABORTED){uploadFailed=true;Update.abort();}
  });
  web.on("/api/wifi",HTTP_POST,[]{if(!authorized(true))return;allOff();portalRequested=true;web.send(200,"application/json","{\"ok\":true}");});
  // WLAN-Suche asynchron: erster Aufruf startet, folgende liefern "running" bis zum Ergebnis.
  web.on("/api/wifi-scan",HTTP_GET,[]{
    if(!authorized())return;JsonDocument d;int n=WiFi.scanComplete();
    if(n==WIFI_SCAN_FAILED){WiFi.scanNetworks(true,false);d["running"]=true;}
    else if(n==WIFI_SCAN_RUNNING)d["running"]=true;
    else{
      d["running"]=false;auto a=d["networks"].to<JsonArray>();
      for(int i=0;i<n&&a.size()<25;i++){String ss=WiFi.SSID(i);if(!ss.length())continue;bool dup=false;for(JsonObject o:a)if(ss==o["ssid"].as<String>()){dup=true;break;}if(dup)continue;
        auto o=a.add<JsonObject>();o["ssid"]=ss;o["rssi"]=WiFi.RSSI(i);o["secure"]=WiFi.encryptionType(i)!=WIFI_AUTH_OPEN;}
      WiFi.scanDelete();
    }
    jsonResponse(200,d);
  });
  // Neues WLAN speichern und neu starten. Wird es nach dem Start nicht gefunden, öffnet nach 30 s das Setup-WLAN.
  web.on("/api/wifi-set",HTTP_POST,[]{
    if(!authorized(true))return;JsonDocument d;if(!readBody(d))return;
    String ssid=d["ssid"]|"",pw=d["password"]|"";
    if(ssid.length()<1||ssid.length()>32||pw.length()>63||(pw.length()&&pw.length()<8)){errorResponse(400,"WLAN-Name 1 bis 32 Zeichen; Passwort leer (offenes WLAN) oder 8 bis 63 Zeichen");return;}
    if(!rememberWifi(ssid,pw)){errorResponse(500,"Speichern fehlgeschlagen");return;}
    allOff();newSsid=ssid;newWifiPassword=pw;web.send(200,"application/json","{\"ok\":true}");rebootAt=millis()+1000;
  });
  // Gespeichertes WLAN löschen und neu starten: danach öffnet das Setup-WLAN (Test der Neueinrichtung).
  // Weiteres WLAN merken, ohne die bestehende Verbindung zu trennen.
  web.on("/api/wifi-add",HTTP_POST,[]{
    if(!authorized(true))return;JsonDocument d;if(!readBody(d))return;
    String ssid=d["ssid"]|"",pw=d["password"]|"";
    if(!validWifi(ssid,pw)){errorResponse(400,"WLAN-Name 1 bis 32 Zeichen; Passwort leer (offenes WLAN) oder 8 bis 63 Zeichen");return;}
    if(!rememberWifi(ssid,pw)){errorResponse(500,"Speichern fehlgeschlagen");return;}
    web.send(200,"application/json","{\"ok\":true}");
  });
  web.on("/api/wifi-remove",HTTP_POST,[]{
    if(!authorized(true))return;JsonDocument d;if(!readBody(d))return;
    int n=wifiIndex(d["ssid"]|"");if(n<0){errorResponse(404,"WLAN nicht gespeichert");return;}
    if(wifiCount<=1){errorResponse(400,"Mindestens ein WLAN muss gespeichert bleiben");return;}
    for(int i=n+1;i<wifiCount;i++)wifis[i-1]=wifis[i];wifiCount--;wifiLastTried=-1;
    if(!saveWifis()){errorResponse(500,"Speichern fehlgeschlagen");return;}
    web.send(200,"application/json","{\"ok\":true}");
  });
  web.on("/api/wifi-reset",HTTP_POST,[]{if(!authorized(true))return;allOff();web.send(200,"application/json","{\"ok\":true}");eraseWifi=true;rebootAt=millis()+1000;});
  web.onNotFound([]{if(authorized())errorResponse(404,"Nicht gefunden");});
}
void discovery(const String& type,const String& id,const String& name,const String& stateTopic,const String& deviceClass="",const String& unit="",const String& commandTopic="") {
  JsonDocument d;d["name"]=name;d["unique_id"]=deviceId+"_"+id;d["state_topic"]=stateTopic;
  d["availability_topic"]=baseTopic+"/availability";d["payload_available"]="online";d["payload_not_available"]="offline";
  if(type=="sensor"||type=="binary_sensor")d["expire_after"]=30;
  if(deviceClass.length())d["device_class"]=deviceClass;if(unit.length())d["unit_of_measurement"]=unit;
  if(type=="sensor"&&unit.length())d["state_class"]="measurement";
  if(commandTopic.length()){d["command_topic"]=commandTopic;d["retain"]=false;}
  auto dev=d["device"].to<JsonObject>();dev["identifiers"].to<JsonArray>().add(deviceId);dev["name"]=cfg.name;dev["manufacturer"]="ETA IO Projekt";dev["model"]="ESP32 IO Bridge";dev["sw_version"]=VERSION;dev["configuration_url"]="http://"+WiFi.localIP().toString()+"/";
  String out;serializeJson(d,out);mqttPublish("homeassistant/"+type+"/"+topicId+"/"+id+"/config",out,true);
}
void publishDiscovery() {
  if(cfg.etaEnabled){
    discovery("sensor","ww","WW Betriebszustand",baseTopic+"/ww");
    discovery("binary_sensor","eta","ETA Verbindung",baseTopic+"/eta","connectivity");
    discovery("binary_sensor","permit","Elektroheizung Freigabe",baseTopic+"/permit");
  }else{
    // Ohne direkte Abfrage gibt es diese Werte nicht: alte Einträge in Home Assistant entfernen
    mqttPublish("homeassistant/sensor/"+topicId+"/ww/config","",true);mqttPublish("homeassistant/binary_sensor/"+topicId+"/eta/config","",true);mqttPublish("homeassistant/binary_sensor/"+topicId+"/permit/config","",true);
  }
  for(int n=0;n<IO_COUNT;n++)if(cfg.io[n].mode!="disabled") {
    String id="io"+String(n), name=cfg.io[n].name.length()?cfg.io[n].name:"IO "+String(n+1);
    bool manual=cfg.io[n].mode=="manual";
    discovery(manual?"switch":"binary_sensor",id,name,baseTopic+"/"+id+"/state","","",manual?baseTopic+"/"+id+"/set":"");
  }
  for(int n=0;n<sensorCount;n++) {
    discovery("sensor",sensors[n].id+"_temperature",sensors[n].name+" Temperatur",baseTopic+"/"+sensors[n].topic+"/temperature","temperature","°C");
    if(!sensors[n].id.startsWith("ds_"))discovery("sensor",sensors[n].id+"_humidity",sensors[n].name+" Feuchte",baseTopic+"/"+sensors[n].topic+"/humidity","humidity","%");
  }
  discoveryNeeded=false;
}
void pointMessage(const String& topic,const String& body) {
  // eta/dev/<benutzer>/state/<punkt> (retained) oder eta/dev/<benutzer>/cmd/<punkt>/result
  String prefix="eta/dev/"+cfg.mqttUser+"/";
  if(!topic.startsWith(prefix))return;
  String rest=topic.substring(prefix.length());bool result=rest.startsWith("cmd/")&&rest.endsWith("/result");
  String key=result?rest.substring(4,rest.length()-7):rest.startsWith("state/")?rest.substring(6):"";
  auto* p=pointState(key);if(!p)return;
  JsonDocument d;
  if(!body.length()){p->at=0;return;}  // gelöschter Zustand: Recht entzogen oder Punkt deaktiviert
  if(deserializeJson(d,body))return;
  if(result){
    String id=d["command_id"]|"";if(p->pendingId.length()&&id!=p->pendingId)return;
    p->status=d["status"]|"unbekannt";String r=d["reason"]|"";if(!r.length())r=d["result"]|"";p->result=r;p->resultAt=millis();p->pendingId="";
    if(d["text"].is<const char*>()){p->text=d["text"].as<String>();p->quality="ok";p->at=millis();}
  }else{
    p->text=d["text"]|"";p->unit=d["unit"]|"";p->quality=d["quality"]|"unbekannt";p->at=millis();
  }
}
void mqttMessage(int length) {
  String topic=mqtt.messageTopic();bool retained=mqtt.messageRetain();String body;
  while(mqtt.available()){char c=mqtt.read();if(body.length()<1024)body+=c;}
  if(topic.startsWith("eta/dev/")){pointMessage(topic,body);return;}
  if(topic=="homeassistant/status"&&body=="online"){discoveryNeeded=true;return;}
  if(retained||length>8)return; // A stored ON command must never restart an output.
  for(int n=0;n<IO_COUNT;n++)if(cfg.io[n].mode=="manual"&&topic==baseTopic+"/io"+n+"/set"&&(body=="ON"||body=="OFF")) {requested[n]=body=="ON";requestedAt[n]=millis();applyControl();lastPublish=0;}
}
void mqttConnectTask(void*) {
  int ok=mqtt.connect(cfg.mqttHost.c_str(),cfg.mqttPort);
  mqttConnectResult=ok?0:mqtt.connectError();mqttConnectDone=true;vTaskDelete(nullptr);
}
String mqttErrorText(int code) {
  switch(code){case -1:return "Zeitueberschreitung";case 4:return "Benutzer oder Passwort falsch";case 5:return "Nicht berechtigt (Konto im Broker fehlt?)";case 3:return "Broker nicht verfuegbar";}
  if(code==-2&&cfg.mqttTls){char buf[96];int e=mqttSecure.lastError(buf,sizeof buf);return e?"TLS: "+String(buf):"Verbindung abgelehnt";}
  return code==-2?"Verbindung abgelehnt":"Fehler "+String(code);
}
void maintainMqtt() {
  if(!cfg.mqtt||WiFi.status()!=WL_CONNECTED)return;
  if(mqttConnecting){
    if(!mqttConnectDone)return;
    mqttConnecting=false;
    if(mqttConnectResult!=0||!mqtt.connected()){mqttError=mqttErrorText(mqttConnectResult);return;}
    mqttError="";
    mqtt.onMessage(mqttMessage);mqtt.subscribe((baseTopic+"/+/set").c_str());mqtt.subscribe("homeassistant/status");
    if(cfg.mqttUser.length()){mqtt.subscribe(("eta/dev/"+cfg.mqttUser+"/state/+").c_str());mqtt.subscribe(("eta/dev/"+cfg.mqttUser+"/cmd/+/result").c_str());}
    mqttPublish(baseTopic+"/availability","online",true);discoveryNeeded=true;
  }
  if(!mqtt.connected()) {
    if(uint32_t(millis()-lastMqtt)<10000)return;lastMqtt=millis();
    mqtt.setClient(cfg.mqttTls?static_cast<Client&>(mqttSecure):static_cast<Client&>(mqttPlain));
    if(cfg.mqttTls){mqttSecure.setCACert(MQTT_CA);mqttSecure.setHandshakeTimeout(8);mqttSecure.setTimeout(8);}
    mqtt.setId(deviceId);mqtt.setUsernamePassword(cfg.mqttUser,cfg.mqttPassword);mqtt.setConnectionTimeout(cfg.mqttTls?8000:1500);mqtt.setKeepAliveInterval(30000);
    mqtt.beginWill((baseTopic+"/availability").c_str(),7,true,1);mqtt.print("offline");mqtt.endWill();
    mqttConnecting=true;mqttConnectDone=false;mqttError="Verbinde ...";
    if(xTaskCreate(mqttConnectTask,"mqtt-connect",12288,nullptr,1,nullptr)!=pdPASS){mqttConnecting=false;mqttError="Speicher fuer Verbindungsaufbau fehlt";}
    return;
  }
  mqtt.poll();if(discoveryNeeded)publishDiscovery();
  if(uint32_t(millis()-lastPublish)<5000)return;lastPublish=millis();
  bool fresh=etaValid&&uint32_t(millis()-etaAt)<uint32_t(cfg.stale)*1000;
  if(cfg.etaEnabled){mqttPublish(baseTopic+"/ww",fresh?wwText():"Unbekannt");mqttPublish(baseTopic+"/eta",fresh?"ON":"OFF");mqttPublish(baseTopic+"/permit",wwPermit(ww,fresh,millis(),etaAt,cfg.stale*1000)?"ON":"OFF");}
  for(int n=0;n<IO_COUNT;n++)if(cfg.io[n].mode!="disabled") {bool state=isInputMode(n)?inputState(n):outputs[n];mqttPublish(baseTopic+"/io"+n+"/state",state?"ON":"OFF");}
  for(int n=0;n<sensorCount;n++) {
    bool valid=sensors[n].valid&&uint32_t(millis()-sensors[n].at)<15000;
    mqttPublish(baseTopic+"/"+sensors[n].topic+"/temperature",valid?String(sensors[n].temperature,2):"None");
    if(!sensors[n].id.startsWith("ds_"))mqttPublish(baseTopic+"/"+sensors[n].topic+"/humidity",valid&&isfinite(sensors[n].humidity)?String(sensors[n].humidity,2):"None");
  }
  // Sammelübersicht (retained): Name, Adresse, Topic und Wert aller Sensoren
  if(sensorCount){
    JsonDocument d;auto a=d.to<JsonArray>();
    for(int n=0;n<sensorCount;n++){bool valid=sensors[n].valid&&uint32_t(millis()-sensors[n].at)<15000;auto o=a.add<JsonObject>();o["id"]=sensors[n].id;o["name"]=sensors[n].name;o["topic"]=baseTopic+"/"+sensors[n].topic+"/temperature";if(valid)o["temperature"]=serialized(String(sensors[n].temperature,2));else o["temperature"]=nullptr;if(valid&&isfinite(sensors[n].humidity))o["humidity"]=serialized(String(sensors[n].humidity,1));}
    String out;serializeJson(d,out);mqttPublish(baseTopic+"/sensors",out,true);
  }
}
void setup() {
  Serial.begin(115200);delay(200);deviceId="eta-io-"+String(uint32_t(ESP.getEfuseMac()>>24),HEX);
  storageOk=prefs.begin("etaio",false);adminPassword=prefs.getString("password",INITIAL_PASSWORD);
  for(int n=0;n<IO_COUNT;n++)cfg.io[n].name="IO "+String(n+1);
  String stored=prefs.getString("config","");
  if(stored.length()){JsonDocument d;Config candidate=cfg;if(!deserializeJson(d,stored)&&!validate(d,candidate).length())cfg=candidate;else {storageOk=false;Serial.println("Konfiguration ungueltig: alle IO deaktiviert.");}}
  topicId=cfg.mqttUser.length()?cfg.mqttUser:deviceId;baseTopic="etaio/"+topicId;
  for(int n=0;n<ACTION_COUNT;n++)trackPoint(cfg.actions[n].key);
  for(int n=0;n<IO_COUNT;n++)if(cfg.io[n].mode=="eta_switch")trackPoint(cfg.io[n].etaKey);
  if(cfg.mqtt)mqttError="Noch nicht verbunden";
  setupPins();setupSensors();setupWeb();
  loadWifis();
  WiFi.mode(WIFI_STA);WiFi.setHostname(hostName().c_str());WiFi.setAutoReconnect(true);WiFi.begin();staSince=millis();
  wifiAttempting=true;wifiAttemptAt=millis();
  esp_task_wdt_init(8,true);esp_task_wdt_add(nullptr);
  Serial.println("ETA IO "+String(VERSION)+" | "+deviceId);Serial.println("USB-Befehle: status, wifi, password-reset. Keine Zugangsdaten im Log.");
  // SSID() describes the current association and can be empty during async
  // reconnect. Query saved station credentials instead of reopening setup.
  if(!wifiManager.getWiFiIsSaved()&&!wifiCount)portalRequested=true;
}
void loop() {
  esp_task_wdt_reset();applyControl();
  if(rebootAt){allOff();if(int32_t(millis()-rebootAt)>=0){if(mqttUp()&&(eraseWifi||newSsid.length())){mqttPublish(baseTopic+"/availability","offline",true);mqtt.stop();}if(eraseWifi){prefs.remove("wifis");wifiManager.resetSettings();}else if(newSsid.length()){WiFi.persistent(true);WiFi.disconnect(false,false);WiFi.begin(newSsid.c_str(),newWifiPassword.length()?newWifiPassword.c_str():nullptr);delay(200);}ESP.restart();}delay(1);return;}
  if(WiFi.status()==WL_CONNECTED)connectedSinceBoot=true;
  if(!portalActive&&!portalRequested)manageWifi();
  if(!portalActive&&!portalRequested&&!connectedSinceBoot&&WiFi.status()!=WL_CONNECTED&&uint32_t(millis()-staSince)>=WIFI_BOOT_WAIT){
    Serial.println("Gespeichertes WLAN nicht gefunden: Setup-WLAN wird geoeffnet.");portalRequested=true;
  }
  if(portalRequested){portalRequested=false;startPortal();}
  if(portalActive){
    wifiManager.process();portalActive=wifiManager.getConfigPortalActive();
    // Portal ohne neue Einrichtung beendet: wieder das gespeicherte WLAN versuchen, danach ggf. erneut Setup-WLAN.
    if(!portalActive&&WiFi.status()!=WL_CONNECTED){WiFi.mode(WIFI_STA);WiFi.begin();staSince=millis();wifiAttempting=true;wifiAttemptAt=millis();wifiLostAt=0;Serial.println("Setup-WLAN beendet, versuche gespeicherte WLANs.");}
  }
  if(WiFi.status()==WL_CONNECTED&&!webStarted&&!portalActive){web.begin();webStarted=true;Serial.println("Web: http://"+WiFi.localIP().toString()+"/");}
  if(WiFi.status()==WL_CONNECTED&&!otaStarted&&!portalActive){
    ArduinoOTA.setHostname(hostName().c_str());ArduinoOTA.setPassword(adminPassword.c_str());
    ArduinoOTA.onStart([]{updating=true;allOff();if(mqttUp()){mqttPublish(baseTopic+"/availability","offline",true);mqtt.stop();}});
    ArduinoOTA.onProgress([](unsigned int,unsigned int){allOff();esp_task_wdt_reset();});
    ArduinoOTA.onError([](ota_error_t){allOff();Serial.println("OTA fehlgeschlagen; Ausgaenge bleiben bis Neustart aus.");});
    ArduinoOTA.begin();otaStarted=true;
  }
  if(otaStarted&&!portalActive)ArduinoOTA.handle();
  if(webStarted)web.handleClient();
  applyControl();if(!portalActive&&!updating){pollEta();applyControl();readSensors();handleEtaSwitches();maintainMqtt();}applyControl();
  while(Serial.available()) {
    char c=Serial.read();if(c=='\n') {
      serialLine.trim();if(serialLine=="wifi")portalRequested=true;
      else if(serialLine=="status"){JsonDocument d;statusJson(d);serializeJson(d,Serial);Serial.println();}
      else if(serialLine=="password-reset"){allOff();prefs.remove("password");adminPassword=INITIAL_PASSWORD;ArduinoOTA.setPassword(adminPassword.c_str());Serial.println("Web-Passwort auf private Zugangskarte zurueckgesetzt.");}
      serialLine="";
    }else if(serialLine.length()<80)serialLine+=c;
  }
  delay(2);
}
