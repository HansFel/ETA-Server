#include "logic.h"
#include <cassert>
#include <iostream>
#include <string>
std::string xml(const char* raw,const char* uri="/user/var/121/10111/0/0/12080") {
 return std::string("<eta xmlns=\"http://www.eta.co.at/rest/v1\"><value uri=\"")+uri+"\">"+raw+"</value></eta>";
}
int main() {
 assert(parseWw(xml("1802").c_str())==WwState::Off);
 assert(parseWw(xml("1803").c_str())==WwState::On);
 assert(parseWw(xml(" 1802\n").c_str())==WwState::Off);
 for(auto raw:{"","0","1804","1802x","1802.0"})assert(parseWw(xml(raw).c_str())==WwState::Unknown);
 assert(parseWw(xml("1802","/user/var/wrong").c_str())==WwState::Unknown);
 assert(parseWw("<html>1802</html>")==WwState::Unknown);
 assert(parseWw("<eta><value>1802</value></eta>")==WwState::Unknown);
 assert(parseWw("<eta xmlns=\"http://www.eta.co.at/rest/v1\"><value uri=\"/user/var/121/10111/0/0/12080\">1802</value><value>1803</value></eta>")==WwState::Unknown);
 assert(!wwPermit(WwState::Unknown,false,100,0,20000));
 assert(!wwPermit(WwState::On,true,100,0,20000));
 assert(!wwPermit(WwState::Off,false,100,0,20000));
 assert(wwPermit(WwState::Off,true,19999,0,20000));
 assert(!wwPermit(WwState::Off,true,20000,0,20000));
 assert(wwPermit(WwState::Off,true,5,UINT32_MAX-5,20));
 assert(!wwPermit(WwState::Off,true,50,UINT32_MAX-5,20));
 assert(manualPermit(true,59999,0,60000));
 assert(!manualPermit(true,60000,0,60000));
 assert(!manualPermit(false,0,0,60000));
 for(int p:{0,1,2,3,4,5,6,7,8,9,10,11,12,15,20,24,28,29,30,31,34,35,36,39})assert(!outputPin(p));
 assert(outputPin(27));assert(inputPin(34));assert(!inputPin(-1));
 // ETA-Punktschlüssel und Werte für MQTT-Befehle
 assert(validPointKey("wiese-switch"));assert(validPointKey("wiese-time-mon-1"));
 for(auto k:{"","Wiese","wiese/switch","wiese+","#","a b","wiese_switch","ä"})assert(!validPointKey(k));
 assert(!validPointKey("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
 assert(validPointValue("Ein"));assert(validPointValue("06:00 - 09:00"));assert(validPointValue("17.5"));
 assert(!validPointValue(""));assert(!validPointValue("a\nb"));assert(!validPointValue("12345678901234567890123456789012345678901"));
 // Entprellung: erster stabiler Zustand nach Start löst nichts aus, Änderungen erst nach Haltezeit
 Debounce d;
 assert(!debounceStep(d,true,0,1000));assert(!debounceStep(d,true,999,1000));
 assert(!debounceStep(d,true,1000,1000));assert(d.known&&d.stable);   // Startzustand: kein Senden
 assert(!debounceStep(d,false,1100,1000));assert(!debounceStep(d,true,1200,1000)); // Prellen
 assert(!debounceStep(d,false,1300,1000));assert(!debounceStep(d,false,2299,1000));
 assert(debounceStep(d,false,2300,1000));assert(!d.stable);            // stabile Änderung: senden
 assert(!debounceStep(d,false,9000,1000));                            // nur einmal
 Debounce w;w.raw=true;w.changedAt=UINT32_MAX-100;
 assert(!debounceStep(w,true,500,1000));assert(debounceStep(w,true,900,1000)==false&&w.known); // Überlauf von millis()
 assert(validHostname("wiesenegger-eta"));assert(validHostname("eta-io-189837"));
 for(auto h:{"","wiesenegger_eta","Wiesenegger","-eta","eta-","eta io","eta.local","abcdefghijklmnopqrstuvwxyz0123456"})assert(!validHostname(h));
 // Sensornamen als Topic-Teil
 auto slug=[](const char* in){char out[33];topicSlug(in,out,sizeof out);return std::string(out);};
 assert(slug("Vorlauf Wiesenegger")=="vorlauf-wiesenegger");
 assert(slug("Rücklauf Süd")=="ruecklauf-sued");assert(slug("Größe ÄÖÜ")=="groesse-aeoeue");
 assert(slug("  WT  nach / vor # 2 ")=="wt-nach-vor-2");assert(slug("+#/")=="");assert(slug("")=="");
 assert(slug("abcdefghijklmnopqrstuvwxyz0123456789abc").size()==32);
 std::cout<<"PASS: strict ETA XML, unknown/stale states, timer boundaries/wraparound, manual timeout, GPIO restrictions, point keys/values, debounce\n";
}
