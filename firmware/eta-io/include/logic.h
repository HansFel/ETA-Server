#pragma once
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <tinyxml2.h>

// Unknown is distinct from OFF. Only a verified, fresh OFF grants permission.
enum class WwState { Unknown, Off, On };
inline bool wwPermit(WwState state, bool valid, uint32_t now, uint32_t last, uint32_t maxAge) {
  return valid && state == WwState::Off && uint32_t(now-last) < maxAge;
}
inline bool manualPermit(bool requested, uint32_t now, uint32_t since, uint32_t duration) {
  return requested && uint32_t(now-since) < duration;
}
inline WwState parseWw(const char* xml) {
  tinyxml2::XMLDocument doc;
  if (doc.Parse(xml) != tinyxml2::XML_SUCCESS) return WwState::Unknown;
  auto root=doc.FirstChildElement("eta");
  if (!root || !root->Attribute("xmlns") || strcmp(root->Attribute("xmlns"),"http://www.eta.co.at/rest/v1")) return WwState::Unknown;
  auto value=root->FirstChildElement("value");
  if (!value || value->NextSiblingElement("value") || !value->GetText()) return WwState::Unknown;
  const char* uri=value->Attribute("uri");
  if (!uri || strcmp(uri,"/user/var/121/10111/0/0/12080")) return WwState::Unknown;
  char* end=nullptr;
  long raw=strtol(value->GetText(),&end,10);
  while (*end==' ' || *end=='\r' || *end=='\n' || *end=='\t') ++end;
  if (*end) return WwState::Unknown;
  return raw==1802 ? WwState::Off : raw==1803 ? WwState::On : WwState::Unknown;
}
inline bool outputPin(int pin) {
  const int pins[]={13,14,16,17,18,19,21,22,23,25,26,27,32,33};
  for (int p:pins) if (p==pin) return true;
  return false;
}
inline bool inputPin(int pin) { return outputPin(pin)||pin==34||pin==35||pin==36||pin==39; }
// ETA-Punktschlüssel der App (z. B. "wiese-switch"): wird Teil eines MQTT-Topics, daher streng.
inline bool validPointKey(const char* key) {
  size_t n=strlen(key);
  if (n<1||n>50) return false;
  for (size_t i=0;i<n;i++) { char c=key[i]; if (!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-')) return false; }
  return true;
}
// Schaltwert (z. B. "Ein", "17.5", "06:00 - 09:00"): 1..40 druckbare Zeichen, Prüfung des Inhalts macht die App.
inline bool validPointValue(const char* value) {
  size_t n=strlen(value);
  if (n<1||n>40) return false;
  for (size_t i=0;i<n;i++) if (static_cast<unsigned char>(value[i])<32||value[i]==127) return false;
  return true;
}
// Hostname im Netz (DHCP, mDNS): 1..32 Zeichen a-z, 0-9, Bindestrich, nicht am Anfang/Ende; Unterstrich ist nicht erlaubt.
inline bool validHostname(const char* h) {
  size_t n=strlen(h);
  if (n<1||n>32||h[0]=='-'||h[n-1]=='-') return false;
  for (size_t i=0;i<n;i++) { char c=h[i]; if (!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-')) return false; }
  return true;
}
// Name -> MQTT-Topic-Teil: Kleinbuchstaben, Umlaute ausgeschrieben, sonst Bindestrich ("Vorlauf Süd" -> "vorlauf-sued").
// Leeres Ergebnis bedeutet: Name nicht als Topic verwendbar (dann bleibt die Sensoradresse).
inline void topicSlug(const char* in, char* out, size_t size) {
  size_t o=0; bool dash=false;
  auto put=[&](char c){ if (o+1<size) out[o++]=c; };
  for (size_t i=0; in[i] && o+1<size; i++) {
    unsigned char c=static_cast<unsigned char>(in[i]);
    const char* rep=nullptr;
    if (c==0xC3 && in[i+1]) {
      switch (static_cast<unsigned char>(in[i+1])) {
        case 0xA4: case 0x84: rep="ae"; break; case 0xB6: case 0x96: rep="oe"; break;
        case 0xBC: case 0x9C: rep="ue"; break; case 0x9F: rep="ss"; break;
      }
      if (rep) { i++; if (dash&&o) put('-'); dash=false; put(rep[0]); put(rep[1]); continue; }
    }
    if ((c>='a'&&c<='z')||(c>='0'&&c<='9')) { if (dash&&o) put('-'); dash=false; put(static_cast<char>(c)); }
    else if (c>='A'&&c<='Z') { if (dash&&o) put('-'); dash=false; put(static_cast<char>(c-'A'+'a')); }
    else dash=true;
  }
  out[o]=0;
}
// Entprellter Schalter: liefert true genau dann, wenn sich der stabile Zustand nach dem ersten
// bekannten Zustand ändert (erster Zustand nach dem Start löst nichts aus).
struct Debounce { bool raw=false, stable=false, known=false; uint32_t changedAt=0; };
inline bool debounceStep(Debounce& d, bool raw, uint32_t now, uint32_t holdMs) {
  if (raw!=d.raw) { d.raw=raw; d.changedAt=now; return false; }
  if ((d.known&&d.stable==raw) || uint32_t(now-d.changedAt)<holdMs) return false;
  bool wasKnown=d.known; d.stable=raw; d.known=true;
  return wasKnown;
}
