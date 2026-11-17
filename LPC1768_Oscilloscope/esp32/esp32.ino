/*
  ARDUINO DUE (ARM Cortex-M3) - THREE-LEVEL SMART ACCESS DEMONSTRATOR
  -------------------------------------------------------------------
  RFID, 4x4 keypad, 2 person IRs, optional door reed, servo, PIR, tamper,
  DS3231, 0.96" SSD1306 (Wire), 1.3" SH1106 (Wire1), LCD 16x2,
  buzzer, LEDs, IR-passage Welford statistics, Z-score, risk, CRC32 audit,
  AES-128-GCM authenticated-encryption demonstration + benchmark.

  This is TABLETOP DEMO firmware, NOT human-safe door control.
  NO ML. NO RTOS. Arduino Due GPIO <= 3.3V only.
  MG996R SERVO signal is routed through Due D40 using Servo.h.
  MG996R VCC requires its OWN regulated 5V supply with common GND;
  do NOT power it using the Arduino Due 5V/3.3V rails.
  No card is authorized until its actual UID is assigned explicitly.
  SIX USER RECORDS are pre-created with the names given by the user.

  Libraries: MFRC522, Servo, RTClib, Adafruit GFX, Adafruit SSD1306,
             Adafruit SH110X, Crypto (by Rhys Weatherley), SD,
             optional hd44780 (Bill Perry); Arduino SPI, Wire.

  Serial 115200 with Newline: HELP, STATUS, USERS, LCDTEST 1, LCDRESET, LCDLIGHT ON/OFF,
  SETUID slot HEX, SAVECARDS, RTC?, SDTEST, LOGTAIL, AUDIT, ATTENDANCE,
  SETTIME YYYY-MM-DD HH:MM:SS, CRYPTOTEST, RESETALARM,
  BUZZTEST, BUZZMODE ACTIVE/PASSIVE/LOW, LEDTEST, SERVOHOME, SERVORESET,
  KEYTEST ON/OFF, KEYWIRE ON/OFF (diagnose 8 keypad conductors).
  PIN ENTER: per-user 1111/1112/1113, then A; B or * clears PIN. # unused.
  ENTRY: outside IR -> RFID -> matching personal PIN + A -> OPEN -> inside IR
         detects crossing -> record RTC entry time -> both IR clear -> CLOSE.
  EXIT: inside IR -> RFID of an ACTIVE INSIDE USER -> servo OPEN (no PIN)
        -> outside IR detects crossing -> both IR clear -> record RTC exit time
        and elapsed stay for THAT RFID identity -> CLOSE.
  Each RFID gets its own occupancy record: supports multiple people inside.
  Sensor-based crossings are estimates, not positive proof of passage.
  RTC must be synchronized to obtain real timestamps and stay duration.

  PINs are demo-only: Vigneshwaran=1111, Sri Harsha=1112, Yogesh=1113.
  Press A to submit; B or * to clear; # is not needed.
  DS3231 shows continuously running real time AFTER sync from local Windows clock.
  Arduino Due cannot know absolute clock time from USB alone without a host sending it.
  microSD hardware remains disconnected; no SD initialization or persistent logs.
  Disable SERIAL_PROVISION after demo credential setup (set to 0),
  restrict physical USB access. UIDs can be cloned; not high-security.
*/

#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <SD.h>
#include <Servo.h>
#include <MFRC522.h>
#include <RTClib.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_SH110X.h>
#include <AES.h>
#include <GCM.h>
#include <SHA256.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>

// ---------------------- CONFIGURATION ------------------------
#define ENABLE_KEYPAD 1            // 4x4 keypad D30..D37; individual PIN per authorized user
#define ENABLE_DOOR_REED 0         // Set 1 AFTER independent door-closed switch is wired D27
#define ENABLE_TAMPER 1            // Set 1 AFTER SW420 OUT wired D28
#define ENABLE_ROOM_PIR 0          // Set 1 AFTER HC-SR501 OUT wired D29
#define ENABLE_RADAR 0             // optional presence radar digital output on D38
#define ENABLE_LARGE_OLED 1        // second I2C bus SDA1/SCL1
#define LARGE_OLED_IS_SH1106 1    // change to 0 for SSD1306
#define ENABLE_LCD_16X2 1          // LCD now connected: 5V backpack via I2C level shifter to Wire
#define SERIAL_PROVISION 1         // set 0 after provisioning (prototype only)
#define ENABLE_HMAC_AUDIT 1       // tamper-demo HMAC key below MUST NOT secure real systems
#define SERVO_DRIVES_DEMO_DOOR 1   // 1=servo moves LIGHT toy door, 0=servo latch only
#define IR_ACTIVE_LOW 1            // most digital IR obstacle sensor modules: LOW=detected
#define REED_CLOSED_LOW 1         // wire reed to GND with INPUT_PULLUP when door closed
#define TAMPER_ACTIVE_LOW 1
#define PIR_ACTIVE_HIGH 1
#define RADAR_ACTIVE_HIGH 1

const uint8_t RFID_SS = 10, RFID_RST = 9, SD_CS = 4;
const uint8_t SERVO_PIN = 40, OUT_IR = 22, IN_IR = 23;  // MG996R signal: D40 (Servo.h)
const uint8_t GREEN_LED = 24, RED_LED = 25, BUZZER = 26;
// BUZZMODE ACTIVE: 3.3V-compatible module/transistor driver that beeps on HIGH.
// BUZZMODE LOW: active-LOW driver/module that beeps on LOW.
// BUZZMODE PASSIVE: piezo or passive buzzer; sketch produces a ~2kHz square wave.
// Choose type from Serial Monitor; software cannot repair incorrect power/wiring.
enum BuzzerMode { BUZZ_ACTIVE_HIGH, BUZZ_ACTIVE_LOW, BUZZ_PASSIVE };
BuzzerMode buzzerMode = BUZZ_ACTIVE_HIGH;
const uint32_t PASSIVE_HALF_PERIOD_US = 250; // 2 kHz nominal, software-timed
bool piezoState = false;
uint32_t lastPiezoFlipUs = 0;
uint32_t ledTestUntil = 0;
bool keypadTestMode = false;
// Electrical test mode: scans all 8 keypad signal pins as possible contact pairs.
// Helps detect missing R4/C4 wiring; does not require a known ribbon order.
bool keypadWireMode = false;
// Safe raw RFID diagnostic: reads any card UID without modifying user/door state.
// Enable with RFIDTEST ON and disable with RFIDTEST OFF.
bool rfidTestMode = false;
const uint8_t KEYPINS[8] = {30,31,32,33,34,35,36,37};
const uint8_t REED_PIN = 27, TAMPER_PIN = 28, PIR_PIN = 29, RADAR_PIN = 38;
const uint8_t ROWS[4] = {30,31,32,33};
const uint8_t COLS[4] = {34,35,36,37};
const uint8_t RTC_I2C = 0x68, SMALL_I2C = 0x3C, LARGE_I2C = 0x3C;
const uint32_t SENSE_DEBOUNCE = 45, CLEAR_CLOSE_MS = 5000;
const uint32_t NO_ENTRY_GRACE_MS = 14000, DOOR_TIMEOUT_MS = 15000;
const uint32_t RFID_COOLDOWN_MS = 600, PIN_TIMEOUT_MS = 16000;
const uint32_t UID_BLOCK_MS = 60000, STATUS_MS = 2000;
// HOME / initial position (physical alignment must be checked with the horn detached).
const uint8_t LOCK_ANGLE = 0, OPEN_ANGLE = 90;
const char *LOGNAME = "ACCESS.CSV";
const char *CFGNAME = "CARDS.CFG";
// Explicit per-person PINs, fixed at boot and checked against the RFID's record.
// These are classroom-demo secrets only; UID and 4-digit PINs are not strong security.
const char *const AUTHORIZED_USER_PINS[3] = {"1111", "1112", "1113"};

// SW-420 DO is DIGITAL. Threshold = number of validated active pulses per
// window, NOT an analog vibration amplitude; adjust module potentiometer for
// physical sensitivity. RAM setting resets to the default after power cycling.
const uint32_t VIBRATION_WINDOW_MS = 1500;
const uint32_t VIBRATION_BOOT_GRACE_MS = 1000;
uint8_t vibrationPulseThreshold = 8;     // adjustable: VIBSET 2..12
bool vibrationArmed = false;
uint32_t servoBootReadyAt = 0;

// Boot servo motion test: command closed, jog +8 deg, return to closed.
// Servo.write() is OPEN LOOP: without a feedback sensor we CANNOT verify
// actual shaft position, mechanical blockage, or electrical servo health.
// Test only with unloaded/toy linkage, and set delta to zero if unsafe.
const uint8_t SERVO_BOOT_JOG_DEG = 8;
const uint32_t SERVO_BOOT_INITIAL_SETTLE_MS = 750;
const uint32_t SERVO_BOOT_JOG_SETTLE_MS = 300;
const uint32_t SERVO_BOOT_FINAL_SETTLE_MS = 650;

const char *ATTNAME = "ATTEND.CSV"; // Paired entry/estimated-exit attendance records
const uint32_t EXIT_CARD_WAIT_MS = 16000; // inside IR triggers request for exit RFID
const uint32_t EXIT_CROSSING_TIMEOUT_MS = 25000; // warning if no outside IR confirmation
// PUBLIC DEMO-ONLY HMAC key. Replace through secure provisioning/key storage
// for actual security. A hardcoded known key cannot protect real access logs.
const uint8_t DEMO_AUDIT_KEY[16]={
  0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,
  0x18,0x19,0x1A,0x1B,0x1C,0x1D,0x1E,0x1F};

MFRC522 rfid(RFID_SS, RFID_RST);
RTC_DS3231 rtc;
Servo servo;
Adafruit_SSD1306 smallOLED(128,64,&Wire,-1);
#if ENABLE_LARGE_OLED
  #if LARGE_OLED_IS_SH1106
  Adafruit_SH1106G largeOLED(128,64,&Wire1,-1);
  #else
  Adafruit_SSD1306 largeOLED(128,64,&Wire1,-1);
  #endif
#endif
#if ENABLE_LCD_16X2
#include <hd44780.h>
#include <hd44780ioClass/hd44780_I2Cexp.h>
hd44780_I2Cexp lcd(0x27); // Confirmed from user logs; 0x3F is not present.
bool lcdOK = false;
char lcdOldLine[2][17] = {{0},{0}};  // Cache LCD rows to avoid display flicker.
int lcdLastIdentity = -2;             // -2=no recent scan; -1=unknown; 0..5=known person.
uint32_t lcdIdentityUntil = 0;       // Display scan identity long enough to read it.
uint32_t lastLCDRefresh = 0;        // Redraw periodically after any transient LCD brownout.
bool lcdLightWanted = true;            // Default ON; switch with LCDLIGHT ON or OFF.
uint32_t lastLightRestore = 0;
char lcdEventLine1[17] = {0};          // Briefly show a user's IN/OUT time.
char lcdEventLine2[17] = {0};
uint32_t lcdEventUntil = 0;

#endif

// Authorized employees have distinct employee IDs and personal demo PINs.
// Empty uid/pin => this record does NOT authorize anything.
struct Person { const char *name; const char *emp; bool allow; char uid[21]; char pin[9]; };
// The six actual UIDs were scanned and assigned to these demo roles in card 1..6 order.
// If CARDS.CFG already exists on SD, its UID assignments override these defaults.
// Label the physical cards to match this list. Three revoked users remain DENIED.
Person people[6] = {
  {"Vigneshwaran","CB.EN.P2EBS26030",true,"93241331","1111"},
  {"Sri Harsha",  "CB.EN.P2EBS26031",true,"93147A21","1112"},
  {"Yogesh",     "CB.EN.P2ENS26032",true,"937C7822","1113"},
  {"Suruthika",  "CB.EN.P2EBS26026",false,"93C9D620",""},
  {"Shrinidhi",  "CB.EN.P2EBS26022",false,"A3D87221",""},
  {"Sri Sakthi", "CB.EN.P2EBS26023",false,"87A2E100",""}
};

struct Debounced {
  uint8_t pin; bool activeHigh; int raw, stable; uint32_t since;
};
Debounced outer = {OUT_IR,!IR_ACTIVE_LOW,HIGH,HIGH,0};
Debounced inner = {IN_IR,!IR_ACTIVE_LOW,HIGH,HIGH,0};
Debounced reed  = {REED_PIN,!REED_CLOSED_LOW,HIGH,HIGH,0};
Debounced pir   = {PIR_PIN,(bool)PIR_ACTIVE_HIGH,LOW,LOW,0};
Debounced radar = {RADAR_PIN,(bool)RADAR_ACTIVE_HIGH,LOW,LOW,0};

bool rtcOK=false, clockOK=false, clockTrusted=false, sdOK=false, smallOK=false, largeOK=false, rfidOK=false;
bool servoAttached=false, authSession=false, entrySeen=false, forcedAlarm=false;
// Access FSM stays in BOOT_HOLD until the commanded servo jog+home completes.
enum ServoBootStage {
  SERVO_BOOT_WAIT_CLEAR,
  SERVO_BOOT_HOME_SETTLE,
  SERVO_BOOT_JOG_MOVING,
  SERVO_BOOT_JOG_SETTLE,
  SERVO_BOOT_RETURN_MOVING,
  SERVO_BOOT_RETURN_SETTLE,
  SERVO_BOOT_READY
};
ServoBootStage servoBootStage=SERVO_BOOT_WAIT_CLEAR;
uint32_t servoBootStageAt=0;
bool servoBootComplete=false;

bool timeoutFlag=false, tamperFlag=false, anomalyFlag=false, enrollmentDirty=false;
bool doorOpened=false, closeWarningLogged=false;
uint32_t lastClock=0, lastRTCprobe=0, lastStatus=0, lastDisplay=0, lastRFID=0;
uint32_t stateSince=0, clearSince=0, openSince=0, authorizationSince=0;
uint32_t lastServoStep=0, buzzerUntil=0, cooldownUntil=0, alarmLast=0;
uint32_t deniedLedUntil=0;
uint32_t scanCount=0, approved=0, denied=0, entryCount=0, intrusionCount=0;
uint32_t seqNo=0, logDropped=0, logFaults=0, observedTimeouts=0;
DateTime clockNow(2000,1,1,0,0,0);
char recentUID[21]="NONE";
char notice[24]="STARTING";
uint32_t noticeUntil=0;
int servoNow=LOCK_ANGLE, servoTarget=LOCK_ANGLE;
int currentPerson=-1;
char enteredPin[9]={0}; uint8_t pinLen=0, wrongPIN=0;
uint32_t pinSince=0;
// Attendance is RAM-backed per credential, with event rows also written to SD.
// A reboot before exit leaves an unmatched ENTRY row in ATTEND.CSV (intentional).
bool insideRecorded[6] = {false,false,false,false,false,false};
char enteredAt[6][22] = {{0}};
uint32_t enteredAtMs[6] = {0};       // fallback only if RTC is invalid
uint32_t enteredEpoch[6] = {0};      // DS3231 UTC-neutral local epoch seconds
uint32_t entryLogged=0, exitLogged=0, exitGrants=0, exitUnconfirmed=0;
bool exitPassage=false, exitOutsideSeen=false, exitCompleted=false, exitUnconfirmedNoted=false, exitCrossingWarning=false;
char exitCrossingAt[22] = {0};
uint32_t exitCrossingEpoch=0, exitCrossingMs=0;
char lastExitName[24]={0}, lastExitIn[22]={0}, lastExitOut[22]={0};
uint32_t lastExitSeconds=0;


// IR passage statistics: independent entry and exit baselines (RAM only).
// ENTRY: grant of matching RFID+PIN -> confirmed inside IR detection.
// EXIT: authorized exit RFID -> confirmed outside IR detection.
// These are PASSAGE timings, NOT measurements of the door position.
struct PassageStats { uint32_t n; double mean, m2, lastSeconds, lastZ; uint32_t anomalies; };
PassageStats entryStats = {}, exitStats = {};
const uint32_t PASSAGE_MIN_MS = 300;       // reject glitches
const uint32_t PASSAGE_MAX_MS = 25000;     // reject abandoned/very long sessions
const uint32_t PASSAGE_WARMUP_SAMPLES = 5;
const double PASSAGE_Z_THRESHOLD = 3.0;
const double PASSAGE_MIN_SIGMA_S = 0.50;   // numerical floor against tiny variance
uint32_t wCount=0;                       // backward-compatible ENTRY fields in STATUS
// Preserve existing risk/status variables, now sourced from entry-passage timing.
double wMean=0.0, wM2=0.0, lastZ=0.0, lastDuration=0.0;

// Data integrity: CRC32 detects incidental record corruption, NOT malicious modification.
struct LogEvent { uint32_t seq, ms; char timestamp[22], action[24], uid[21], state[18], detail[45]; int risk; };
const uint8_t QCAP=14;
LogEvent events[QCAP]; uint8_t qHead=0,qTail=0,qCount=0;

// Bounded per-UID denial and lockout tracking (resets at reboot).
struct Failure { char uid[21]; uint8_t count; uint32_t blockUntil; };
Failure failures[12] = {};

volatile uint16_t tamperEdges=0; volatile uint32_t lastTamperUs=0;
uint32_t windowStart=0; uint16_t windowCount=0;

char serialLine[112]; uint8_t serialLen=0;
char lastKey=0; uint32_t keySince=0;
const char KEYMAP[4][4]={{'1','2','3','A'},{'4','5','6','B'},{'7','8','9','C'},{'*','0','#','D'}};

enum Mode { BOOT_HOLD, IDLE, WAIT_CARD, WAIT_EXIT_CARD, WAIT_PIN, OPENING, WAIT_ENTRY, OPEN_HOLD, CLOSING, WAIT_REED, ALARM };
Mode mode=BOOT_HOLD;
const char* modeText() {
  switch(mode){
    case BOOT_HOLD:return "BOOT HOLD"; case IDLE:return "WAIT PERSON";
    case WAIT_CARD:return "ENTRY RFID"; case WAIT_EXIT_CARD:return "EXIT RFID";
    case WAIT_PIN:return "ENTER PIN";
    case OPENING:return "OPENING"; case WAIT_ENTRY:return "WAIT ENTRY";
    case OPEN_HOLD:return "HOLD OPEN"; case CLOSING:return "CLOSING";
    case WAIT_REED:return "CLOSE DOOR"; case ALARM:return "INTRUSION";
  }
  return "UNKNOWN";
}
bool due(uint32_t since,uint32_t interval,uint32_t now) { return (uint32_t)(now-since)>=interval; }
bool deadlineActive(uint32_t deadline,uint32_t now){ return (int32_t)(deadline-now)>0; }
bool sensed(const Debounced &s){return s.activeHigh ? s.stable==HIGH : s.stable==LOW;}
bool instant(const Debounced &s){return s.activeHigh ? s.raw==HIGH : s.raw==LOW;}
bool doorwayBusy(){return sensed(outer)||sensed(inner)||instant(outer)||instant(inner);}
bool indoorMotion(){
  return (ENABLE_ROOM_PIR && sensed(pir)) || (ENABLE_RADAR && sensed(radar));
}
bool reedClosed(){return ENABLE_DOOR_REED && sensed(reed);}
bool probe(TwoWire &w,uint8_t addr){w.beginTransmission(addr);return w.endTransmission()==0;}
void stamp(char *out,size_t len){
  if(!clockOK){snprintf(out,len,"RTC_INVALID");return;}
  snprintf(out,len,"%04u-%02u-%02u %02u:%02u:%02u",(unsigned)clockNow.year(),(unsigned)clockNow.month(),(unsigned)clockNow.day(),(unsigned)clockNow.hour(),(unsigned)clockNow.minute(),(unsigned)clockNow.second());
}
uint32_t crcUpdate(uint32_t crc,const uint8_t *data,size_t n){
  for(size_t i=0;i<n;++i){crc^=data[i];for(uint8_t j=0;j<8;++j)crc=(crc>>1)^(0xEDB88320UL & (0UL-(crc&1UL)));}
  return crc;
}
uint32_t crcText(const char *s){return ~crcUpdate(0xFFFFFFFFUL,(const uint8_t*)s,strlen(s));}
void hmacTag16(const char *text,char out[33]){
  uint8_t digest[16]; SHA256 mac;
  mac.resetHMAC(DEMO_AUDIT_KEY,sizeof(DEMO_AUDIT_KEY));
  mac.update(text,strlen(text));
  mac.finalizeHMAC(DEMO_AUDIT_KEY,sizeof(DEMO_AUDIT_KEY),digest,sizeof(digest));
  for(size_t i=0;i<sizeof(digest);++i)snprintf(out+i*2,3,"%02X",digest[i]);
  out[32]=0;
}
void setNotice(const char *s,uint32_t ms=1700){snprintf(notice,sizeof(notice),"%s",s);noticeUntil=millis()+ms;}
void buzzerQuiet(){
  digitalWrite(BUZZER, buzzerMode == BUZZ_ACTIVE_LOW ? HIGH : LOW);
  piezoState=false;
}
void buzz(uint32_t ms){
  buzzerUntil=millis()+ms;
  if(buzzerMode==BUZZ_ACTIVE_HIGH) digitalWrite(BUZZER,HIGH);
  else if(buzzerMode==BUZZ_ACTIVE_LOW) digitalWrite(BUZZER,LOW);
  else {lastPiezoFlipUs=micros(); piezoState=true; digitalWrite(BUZZER,HIGH);}
}
void buzzerTick(uint32_t now){
  if(!buzzerUntil)return;
  if(!deadlineActive(buzzerUntil,now)){
    buzzerUntil=0; buzzerQuiet(); return;
  }
  if(buzzerMode==BUZZ_PASSIVE){
    uint32_t us=micros();
    if((uint32_t)(us-lastPiezoFlipUs)>=PASSIVE_HALF_PERIOD_US){
      lastPiezoFlipUs=us;
      piezoState=!piezoState;
      digitalWrite(BUZZER,piezoState?HIGH:LOW);
    }
  }
}
uint8_t riskScore(){
  uint32_t r=0;
  if(denied) r+=((denied*5UL>25UL)?25UL:denied*5UL);
  if(timeoutFlag)r+=20;
  if(tamperFlag)r+=30;
  if(forcedAlarm)r+=45;
  if(anomalyFlag)r+=15;
  return (uint8_t)(r>100UL?100UL:r);
}
void addEvent(const char *action,const char *detail="",const char *uid=NULL){
  char ts[22];stamp(ts,sizeof(ts));++seqNo;
  Serial.print("[EVENT #");Serial.print(seqNo);Serial.print(" ");Serial.print(ts);
  Serial.print("] ");Serial.print(action);Serial.print(" | ");Serial.print(detail);
  Serial.print(" | UID=");Serial.print(uid?uid:recentUID);
  Serial.print(" | risk=");Serial.println(riskScore());
  if(!sdOK)return; // No SD module: serial events only; prevent queue overflow.
  if(qCount>=QCAP){logDropped++;Serial.println("[LOG] RAM queue overflow; record lost");return;}
  LogEvent &e=events[qTail];e.seq=seqNo;e.ms=millis();e.risk=riskScore();
  snprintf(e.timestamp,sizeof(e.timestamp),"%s",ts);
  snprintf(e.action,sizeof(e.action),"%s",action);
  snprintf(e.uid,sizeof(e.uid),"%s",uid?uid:recentUID);
  snprintf(e.state,sizeof(e.state),"%s",modeText());
  snprintf(e.detail,sizeof(e.detail),"%s",detail);
  qTail=(qTail+1)%QCAP;qCount++;
}
void changeMode(Mode next,const char *why){
  if(mode==next)return;mode=next;stateSince=millis();
  addEvent("STATE",why);
}
void emitCSV(const LogEvent &e,char *buf,size_t cap){
  // Fields are generated by firmware and contain no commas/newlines.
  snprintf(buf,cap,"%lu,%s,%lu,%s,%s,%s,%u,%s",
    (unsigned long)e.seq,e.timestamp,(unsigned long)e.ms,e.action,
    e.uid,e.state,(unsigned)e.risk,e.detail);
}
bool startSD(){
  digitalWrite(RFID_SS,HIGH);digitalWrite(SD_CS,HIGH);
  if(!SD.begin(SD_CS)){sdOK=false;Serial.println("[SD] Failed. Check SPI main header, CS D4, FAT32, 3.3V levels.");return false;}
  sdOK=true;
  if(!SD.exists(LOGNAME)){
    File f=SD.open(LOGNAME,FILE_WRITE);
    if(!f){sdOK=false;return false;}
    f.println("seq,rtc_timestamp,uptime_ms,event,uid,state,risk,details,crc32,hmac_sha256_128");f.close();
  }
  if(!SD.exists(ATTNAME)){
    File a=SD.open(ATTNAME,FILE_WRITE);
    if(a){a.println("event,entry_time,exit_time,duration_seconds,name,employee_id,uid,certainty");a.close();}
    else Serial.println("[SD] WARNING: cannot create ATTEND.CSV");
  }
  Serial.println("[SD] ACCESS.CSV ready; attendance file checked");return true;
}
void flushLog(){
  static uint32_t prev=0;uint32_t now=millis();
  if(!sdOK||!qCount||!due(prev,180,now))return;
  prev=now;
  digitalWrite(RFID_SS,HIGH);
  File f=SD.open(LOGNAME,FILE_WRITE);
  if(!f){sdOK=false;logFaults++;Serial.println("[SD] Write failed; queue retained");return;}
  LogEvent &e=events[qHead];char line[230];emitCSV(e,line,sizeof(line));
  char base[260];
  snprintf(base,sizeof(base),"%s,%08lX",line,(unsigned long)crcText(line));
#if ENABLE_HMAC_AUDIT
  char tag[33];hmacTag16(base,tag);
#else
  const char *tag="DISABLED";
#endif
  f.print(base);f.print(',');f.println(tag);f.close();
  qHead=(qHead+1)%QCAP;qCount--;
}
// Append paired attendance details to SD. ACCESS.CSV also records all events;
// an attendance SD failure never silently authorizes anyone.
void attendanceRow(const char *action,int slot,const char *timeIn,const char *timeOut,
                   uint32_t seconds,const char *certainty){
  if(slot<0||slot>=6||!sdOK)return;
  digitalWrite(RFID_SS,HIGH);
  File f=SD.open(ATTNAME,FILE_WRITE);
  if(!f){Serial.println("[ATTEND] ATTEND.CSV open FAILED");return;}
  f.print(action);f.print(',');f.print(timeIn);f.print(',');f.print(timeOut);
  f.print(',');f.print(seconds);f.print(',');f.print(people[slot].name);
  f.print(',');f.print(people[slot].emp);f.print(',');f.print(people[slot].uid);
  f.print(',');f.println(certainty);
  f.close();
}
void attendanceLCD(int slot,bool entering,const char* timestamp,uint32_t now){
#if ENABLE_LCD_16X2
  if(slot<0||slot>=6)return;
  snprintf(lcdEventLine1,sizeof(lcdEventLine1),"%s",people[slot].name);
  if(strlen(timestamp)>=19 && strcmp(timestamp,"RTC_INVALID"))
    snprintf(lcdEventLine2,sizeof(lcdEventLine2),"%s %.8s",entering?"IN ":"OUT",timestamp+11);
  else snprintf(lcdEventLine2,sizeof(lcdEventLine2),"%s RTC INVALID",entering?"IN":"OUT");
  lcdEventUntil=now+12000;
  lcdOldLine[0][0]=0;lcdOldLine[1][0]=0;
#else
  (void)slot;(void)entering;(void)timestamp;(void)now;
#endif
}
int countInside(){int n=0;for(int i=0;i<6;++i)if(insideRecorded[i])n++;return n;}
void observePassage(PassageStats &stats, const char *direction, uint32_t elapsedMs,
                    const char *uid, bool excludeFromBaseline) {
  if(elapsedMs<PASSAGE_MIN_MS || elapsedMs>PASSAGE_MAX_MS || excludeFromBaseline){
    Serial.print("[PASSAGE] ");Serial.print(direction);
    Serial.println(" ignored (invalid duration or interrupted session)");
    return;
  }
  const double seconds=(double)elapsedMs/1000.0;
  stats.lastSeconds=seconds;
  stats.lastZ=0.0;
  bool outlier=false;
  // Compare to PRIOR baseline, then decide whether to learn the new observation.
  if(stats.n>=PASSAGE_WARMUP_SAMPLES){
    const double variance=stats.n>1?stats.m2/(double)(stats.n-1):0.0;
    const double sigma=sqrt(variance);
    const double denom=sigma>PASSAGE_MIN_SIGMA_S?sigma:PASSAGE_MIN_SIGMA_S;
    stats.lastZ=(seconds-stats.mean)/denom;
    outlier=fabs(stats.lastZ)>=PASSAGE_Z_THRESHOLD;
  }
  if(outlier){
    ++stats.anomalies;
    anomalyFlag=true;
    char text[45];snprintf(text,sizeof(text),"%s passage %.2fs z=%.2f",direction,seconds,stats.lastZ);
    addEvent("PASSAGE_Z_WARNING",text,uid);
    setNotice("PASSAGE TIME WARNING",2500);
  }else{
    ++stats.n;
    const double delta=seconds-stats.mean;
    stats.mean+=delta/(double)stats.n;
    stats.m2+=delta*(seconds-stats.mean);
  }
  if(&stats==&entryStats){
    wCount=stats.n;wMean=stats.mean;wM2=stats.m2;
  }
  lastDuration=seconds;lastZ=stats.lastZ;
  Serial.print("[PASSAGE] ");Serial.print(direction);
  Serial.print(" elapsed_s=");Serial.print(seconds,2);
  Serial.print(" N=");Serial.print(stats.n);
  Serial.print(" mean_s=");Serial.print(stats.mean,2);
  Serial.print(" z=");Serial.print(stats.lastZ,2);
  Serial.println(outlier?" ANOMALY":" NORMAL/WARMUP");
}


void recordEntry(uint32_t now){
  if(currentPerson<0||currentPerson>=6 || !people[currentPerson].allow)return;
  char ts[22];stamp(ts,sizeof(ts));
  char details[45];snprintf(details,sizeof(details),"%s confirmed by inside IR",people[currentPerson].name);
  if(insideRecorded[currentPerson]){
    addEvent("ENTRY_DUPLICATE",details,people[currentPerson].uid);
    return;
  }
  insideRecorded[currentPerson]=true;
  snprintf(enteredAt[currentPerson],sizeof(enteredAt[currentPerson]),"%s",ts);
  enteredAtMs[currentPerson]=now;
  // Use actual DS3231 time, not uptime, for accurate multi-hour stays.
  if(rtcOK){clockNow=rtc.now();clockOK=clockTrusted&&clockNow.isValid()&&clockNow.year()>=2020;}
  stamp(ts,sizeof(ts));
  enteredEpoch[currentPerson]=clockOK?clockNow.unixtime():0;
  snprintf(enteredAt[currentPerson],sizeof(enteredAt[currentPerson]),"%s",ts);
  // An entry sample is valid only after RFID+PIN grant and inside-IR confirmation.
  observePassage(entryStats,"ENTRY",(uint32_t)(now-authorizationSince),
                 people[currentPerson].uid,timeoutFlag||forcedAlarm||tamperFlag);
  entryLogged++;
  addEvent("PERSON_ENTERED",details,people[currentPerson].uid);
  attendanceRow("ENTRY",currentPerson,ts,"",0,"INSIDE_IR_AFTER_PIN");
  attendanceLCD(currentPerson,true,ts,now);
  Serial.print("[ATTENDANCE] ENTER ");Serial.print(people[currentPerson].name);
  Serial.print(" at ");Serial.println(ts);
  setNotice("ENTRY RECORDED",2600);
}
// Inside occupancy is tracked PER RFID. Exit must be initiated with that card.
// RTC timestamp is captured when the OUTSIDE IR sees passage after opening;
// the record commits only after both IR sensors clear.
uint32_t secondsInside(int slot,uint32_t now){
  if(slot<0||slot>=6||!insideRecorded[slot])return 0;
  if(clockOK && enteredEpoch[slot]){
    uint32_t ts=clockNow.unixtime();
    if(ts>=enteredEpoch[slot])return ts-enteredEpoch[slot];
  }
  if(enteredAtMs[slot])return (uint32_t)(now-enteredAtMs[slot])/1000UL;
  return 0;
}
void durationHMS(uint32_t seconds,char *dest,size_t cap){
  unsigned long hh=(unsigned long)seconds/3600UL;
  unsigned long mm=(unsigned long)(seconds%3600UL)/60UL;
  unsigned long ss=(unsigned long)seconds%60UL;
  snprintf(dest,cap,"%02lu:%02lu:%02lu",hh,mm,ss);
}
void recordRFIDExit(uint32_t now){
  const int who=currentPerson;
  if(!exitPassage||exitCompleted||!exitOutsideSeen||who<0||who>=6||!insideRecorded[who])return;
  uint32_t duration=0;
  const bool reliable=enteredEpoch[who] && exitCrossingEpoch>=enteredEpoch[who];
  if(reliable)duration=exitCrossingEpoch-enteredEpoch[who];
  else if(enteredAtMs[who])duration=(uint32_t)(exitCrossingMs-enteredAtMs[who])/1000UL;
  char details[45];
  snprintf(details,sizeof(details),"%s RFID OUT stay=%lus",people[who].name,(unsigned long)duration);
  addEvent("PERSON_EXITED_RFID",details,people[who].uid);
  attendanceRow("EXIT_RFID_IR",who,enteredAt[who],exitCrossingAt,duration,
                reliable?"RFID_OUTSIDE_IR":"RTC_INVALID_OR_UPTIME_ONLY");
  attendanceLCD(who,false,exitCrossingAt,now);
  snprintf(lastExitName,sizeof(lastExitName),"%s",people[who].name);
  snprintf(lastExitIn,sizeof(lastExitIn),"%s",enteredAt[who]);
  snprintf(lastExitOut,sizeof(lastExitOut),"%s",exitCrossingAt);
  lastExitSeconds=duration;
  char pretty[24];durationHMS(duration,pretty,sizeof(pretty));
  Serial.print("[ATTENDANCE] EXIT CONFIRMED BY RFID + IR: ");Serial.println(people[who].name);
  Serial.print("  ENTRY: ");Serial.println(enteredAt[who]);
  Serial.print("  EXIT : ");Serial.println(exitCrossingAt);
  Serial.print("  STAY : ");Serial.print(pretty);
  Serial.println(reliable?" (DS3231 clock)":" (ESTIMATE: RTC invalid/restarted)");
  insideRecorded[who]=false;enteredAt[who][0]=0;enteredEpoch[who]=0;enteredAtMs[who]=0;
  // Completed authenticated exit; measure RFID grant -> outside IR crossing.
  observePassage(exitStats,"EXIT",(uint32_t)(exitCrossingMs-authorizationSince),
                 people[who].uid,timeoutFlag||forcedAlarm||tamperFlag||exitCrossingWarning);
  exitLogged++;exitCompleted=true;exitOutsideSeen=false;
  setNotice("EXIT TIME RECORDED",2500);
}
// Attendance recovery when a session spans an Arduino reboot. Requires working SD.
// Replays ATTEND.CSV in order, matching RFID UID (not a guessed identity).
int matchCard(const char *uid); // Defined in RFID credentials section.
void restoreInsideFromSD(){
  if(!sdOK || !SD.exists(ATTNAME)){
    Serial.println("[ATTEND] No SD attendance recovery; active sessions will be lost on reset");
    return;
  }
  digitalWrite(RFID_SS,HIGH);
  File f=SD.open(ATTNAME,FILE_READ);
  if(!f){Serial.println("[ATTEND] Cannot read ATTEND.CSV");return;}
  char line[225];
  while(f.available()){
    size_t n=f.readBytesUntil('\n',line,sizeof(line)-1);
    line[n]=0;
    if(n==0)continue;
    // Skip long/invalid records to avoid associating the wrong person.
    if(n==sizeof(line)-1 && f.peek()!='\n' && f.available()){
      while(f.available() && f.read()!='\n'){}
      continue;
    }
    // Keep empty CSV fields intact: ENTRY rows intentionally have empty exit_time.
    char *fields[8]={0};
    fields[0]=line;char *cursor=line;bool valid=true;
    for(uint8_t i=1;i<8;++i){
      char *comma=strchr(cursor,',');
      if(!comma){valid=false;break;}
      *comma=0;cursor=comma+1;fields[i]=cursor;
    }
    if(!valid || !fields[6] || !fields[6][0])continue;
    int who=matchCard(fields[6]);
    if(who<0 || !people[who].allow)continue;
    if(!strcmp(fields[0],"ENTRY")){
      if(!fields[1] || strlen(fields[1])<19 || !strcmp(fields[1],"RTC_INVALID"))continue;
      int y,mo,d,h,mi,se;
      if(sscanf(fields[1],"%d-%d-%d %d:%d:%d",&y,&mo,&d,&h,&mi,&se)!=6)continue;
      DateTime t(y,mo,d,h,mi,se);
      if(!t.isValid() || t.year()<2020)continue;
      insideRecorded[who]=true;snprintf(enteredAt[who],sizeof(enteredAt[who]),"%s",fields[1]);
      enteredEpoch[who]=t.unixtime();enteredAtMs[who]=0;
    }else if(!strcmp(fields[0],"EXIT_RFID_IR") || !strcmp(fields[0],"EXIT_EST")){
      insideRecorded[who]=false;enteredEpoch[who]=0;enteredAt[who][0]=0;enteredAtMs[who]=0;
    }
  }
  f.close();
  Serial.print("[ATTEND] Recovered active inside users from SD: ");Serial.println(countInside());
}
void timestampTick(uint32_t now){
  if(rtcOK && due(lastClock,1000,now)){
    lastClock=now;
    if(!probe(Wire,RTC_I2C)){
      rtcOK=clockOK=false;Serial.println("[RTC] Lost 0x68 ACK");addEvent("RTC_LOST");
    } else {
      clockNow=rtc.now();clockOK=clockTrusted&&clockNow.isValid()&&clockNow.year()>=2020&&clockNow.year()<2100;
    }
  }
  if(!rtcOK && due(lastRTCprobe,5000,now)){
    lastRTCprobe=now;
    if(probe(Wire,RTC_I2C)&&rtc.begin(&Wire)){
      rtcOK=true;clockNow=rtc.now();clockTrusted=!rtc.lostPower();clockOK=clockTrusted&&clockNow.isValid()&&clockNow.year()>=2020;
      Serial.println("[RTC] Reconnected");addEvent("RTC_OK");
    }
  }
}
void beginDebounced(Debounced &s){pinMode(s.pin,INPUT_PULLUP);s.raw=s.stable=digitalRead(s.pin);s.since=millis();}
void updateDebounced(Debounced &s,uint32_t now,const char *name){
  int v=digitalRead(s.pin);
  if(v!=s.raw){s.raw=v;s.since=now;}
  if(s.stable!=s.raw && due(s.since,SENSE_DEBOUNCE,now)){
    bool previous=sensed(s);s.stable=s.raw;
    if(previous!=sensed(s))addEvent(sensed(s)?"SENSOR_ON":"SENSOR_OFF",name);
  }
}
void handleDoorReed(uint32_t now){
  if(!ENABLE_DOOR_REED)return;
  static bool lastClosed=true;static bool first=true;
  bool closed=reedClosed();
  if(first){lastClosed=closed;first=false;return;}
  if(closed==lastClosed)return;
  lastClosed=closed;
  if(!closed){
    openSince=now;doorOpened=true;timeoutFlag=false;
    addEvent("DOOR_OPEN","Reed sensed open");
    if(!authSession && mode!=OPENING && !forcedAlarm){
      forcedAlarm=true;intrusionCount++;
      mode=ALARM;servoTarget=servoNow; // do NOT drive latch closed into a forced-open doorway
      addEvent("FORCED_OPEN","No authorized session");buzz(600);setNotice("INTRUSION!",5000);
    }
  }else{
    addEvent("DOOR_CLOSED","Reed sensed closed");
    if(doorOpened && authSession){
      // Door reed is optional feedback only; statistical learning is from IR-confirmed
      // authenticated passages, not from mechanical door-open duration.
    }
    doorOpened=false;timeoutFlag=false;
    if(mode==WAIT_REED && !doorwayBusy()){
      servoTarget=LOCK_ANGLE;changeMode(CLOSING,"reed confirms closed");
    }
  }
}
void IRs(uint32_t now){
  updateDebounced(outer,now,"outside IR");updateDebounced(inner,now,"inside IR");
  if(ENABLE_DOOR_REED){updateDebounced(reed,now,"door reed");handleDoorReed(now);}
  if(ENABLE_ROOM_PIR)updateDebounced(pir,now,"room PIR motion");
  if(ENABLE_RADAR)updateDebounced(radar,now,"room radar presence");
}
void tamperIRQ(){
  uint32_t us=micros();
  if((uint32_t)(us-lastTamperUs)<12000)return;
  lastTamperUs=us;
  if(digitalRead(TAMPER_PIN)==(TAMPER_ACTIVE_LOW?LOW:HIGH))tamperEdges++;
}
void tamperCheck(uint32_t now){
  if(!ENABLE_TAMPER)return;
  // Ignore startup motor's vibration so it cannot trip its own tamper latch.
  // New vibration events are armed only after servo homing and a quiet grace.
  if(!servoBootComplete || !due(servoBootReadyAt,VIBRATION_BOOT_GRACE_MS,now)){
    noInterrupts();tamperEdges=0;interrupts();
    windowCount=0;windowStart=now;
    return;
  }
  if(!vibrationArmed){
    vibrationArmed=true;
    windowCount=0;windowStart=now;
    Serial.print("[VIB] ARMED: threshold=");Serial.print(vibrationPulseThreshold);
    Serial.print(" active pulses per ");Serial.print(VIBRATION_WINDOW_MS);
    Serial.println(" ms (SW-420 DO is digital)");
  }
  if(windowStart==0)windowStart=now;
  uint16_t n;
  noInterrupts();n=tamperEdges;tamperEdges=0;interrupts();
  windowCount=(uint16_t)(((uint32_t)windowCount+n>65535UL)?65535UL:(uint32_t)windowCount+n);
  if(due(windowStart,VIBRATION_WINDOW_MS,now)){
    // Software pulse-count threshold filters short accidental vibrations.
    // A qualifying event LATCHES, even after vibration stops.
    if(windowCount>=vibrationPulseThreshold && !tamperFlag){
      tamperFlag=true;alarmLast=now;intrusionCount++;
      rfidTestMode=false;             // Test modes must never bypass alarm acknowledgement.
      keypadWireMode=false;keypadTestMode=false;
      lastKey=0;
      servoTarget=servoNow;           // Do not force the toy door against an obstruction.
      char tamperInfo[45];
      snprintf(tamperInfo,sizeof(tamperInfo),"%u pulses/%lu ms; auth RFID required",
               (unsigned)vibrationPulseThreshold,(unsigned long)VIBRATION_WINDOW_MS);
      addEvent("TAMPER",tamperInfo);
      changeMode(ALARM,"SW420 tamper; scan an authorized RFID card");
      setNotice("TAMPER DETECTED",3500);
      Serial.println("[TAMPER] LATCHED. Scan AUTHORIZED RFID to stop buzzer (no PIN).");
    }
    windowStart=now;windowCount=0;
  }
  // Keep the buzzer sounding until an authorized RFID actually clears the latch.
  // Recharge the timer before expiration. Works in active and passive buzzer modes.
  if(tamperFlag && (!buzzerUntil || !deadlineActive(buzzerUntil,now) ||
      (uint32_t)(buzzerUntil-now)<250U))buzz(700);
}
void motorTick(uint32_t now){
  if(!servoAttached||servoNow==servoTarget)return;
  if(!due(lastServoStep,16,now))return;
  lastServoStep=now;
  servoNow+=(servoTarget>servoNow)?1:-1;
  servo.write(servoNow);
}
const char* servoBootStageName(){
  switch(servoBootStage){
    case SERVO_BOOT_WAIT_CLEAR:return "WAIT_CLEAR";
    case SERVO_BOOT_HOME_SETTLE:return "HOME_SETTLE";
    case SERVO_BOOT_JOG_MOVING:return "JOG_OUT";
    case SERVO_BOOT_JOG_SETTLE:return "JOG_SETTLE";
    case SERVO_BOOT_RETURN_MOVING:return "RETURN_HOME";
    case SERVO_BOOT_RETURN_SETTLE:return "FINAL_SETTLE";
    case SERVO_BOOT_READY:return "READY";
  }
  return "UNKNOWN";
}
void safeServoInit(uint32_t now){
  static bool reportedOpen=false;
  if(servoBootComplete)return;
  // Never command boot movement if a person is at either doorway IR.
  if(doorwayBusy()){
    // Stop a boot jog at its current COMMAND if an IR becomes active.
    if(servoAttached && servoBootStage!=SERVO_BOOT_WAIT_CLEAR){
      servoTarget=servoNow;
      servoBootStage=SERVO_BOOT_WAIT_CLEAR;
      Serial.println("[SERVO BOOT] Paused: doorway IR active; wait until both clear.");
    }
    return;
  }
  // A physically open door cannot be assumed safe to drive closed.
  if(ENABLE_DOOR_REED && !reedClosed()){
    if(!reportedOpen){
      reportedOpen=true;
      addEvent("STARTUP_DOOR_OPEN","Close physical door before servo check");
      setNotice("CLOSE DOOR FIRST",4000);
    }
    return;
  }
  if(!servoAttached){
    // Servo command home: this is NOT an actual encoder/limit-switch check.
    servo.write(LOCK_ANGLE);
    servo.attach(SERVO_PIN);
    servo.write(LOCK_ANGLE);
    servoAttached=true;
    servoNow=servoTarget=LOCK_ANGLE;
    servoBootStage=SERVO_BOOT_HOME_SETTLE;
    servoBootStageAt=now;
    Serial.print("[SERVO BOOT] Attached D"); Serial.print(SERVO_PIN); Serial.print("; commanded CLOSED ");
    Serial.print(LOCK_ANGLE);Serial.println(" deg. Waiting to settle.");
    setNotice("SERVO STARTUP TEST",1200);
    return;
  }
  if(servoBootStage==SERVO_BOOT_WAIT_CLEAR){
    // On a prior interrupted jog, return to home before becoming ready.
    servoTarget=LOCK_ANGLE;
    servoBootStage=(servoNow==LOCK_ANGLE)?SERVO_BOOT_HOME_SETTLE:SERVO_BOOT_RETURN_MOVING;
    servoBootStageAt=now;
    Serial.println("[SERVO BOOT] IR clear; resuming safe closed-position command.");
  } else if(servoBootStage==SERVO_BOOT_HOME_SETTLE &&
            due(servoBootStageAt,SERVO_BOOT_INITIAL_SETTLE_MS,now)){
    if(SERVO_BOOT_JOG_DEG>0){
      servoTarget=(int)LOCK_ANGLE+SERVO_BOOT_JOG_DEG;
      if(servoTarget>180)servoTarget=180;
      servoBootStage=SERVO_BOOT_JOG_MOVING;
      Serial.print("[SERVO BOOT] Commanded short test jog to ");
      Serial.print(servoTarget);Serial.println(" deg.");
    }else{
      servoTarget=LOCK_ANGLE;
      servoBootStage=SERVO_BOOT_RETURN_SETTLE;
      servoBootStageAt=now;
    }
  } else if(servoBootStage==SERVO_BOOT_JOG_MOVING && servoNow==servoTarget){
    servoBootStage=SERVO_BOOT_JOG_SETTLE;
    servoBootStageAt=now;
  } else if(servoBootStage==SERVO_BOOT_JOG_SETTLE &&
            due(servoBootStageAt,SERVO_BOOT_JOG_SETTLE_MS,now)){
    servoTarget=LOCK_ANGLE;
    servoBootStage=SERVO_BOOT_RETURN_MOVING;
    Serial.println("[SERVO BOOT] Commanded servo back to CLOSED/home.");
  } else if(servoBootStage==SERVO_BOOT_RETURN_MOVING && servoNow==LOCK_ANGLE){
    servoBootStage=SERVO_BOOT_RETURN_SETTLE;
    servoBootStageAt=now;
  } else if(servoBootStage==SERVO_BOOT_RETURN_SETTLE &&
            due(servoBootStageAt,SERVO_BOOT_FINAL_SETTLE_MS,now)){
    servoBootStage=SERVO_BOOT_READY;
    servoBootComplete=true;
    servoBootReadyAt=now;
    Serial.println("[SERVO BOOT] Command sequence completed; CLOSED angle requested.");
    Serial.println("[SERVO BOOT] NO position feedback: visually verify actual servo movement.");
    Serial.println("[SERVO BOOT] Access enabled; vibration filter arms after 1 second.");
    addEvent("SERVO_BOOT_OK","Commanded jog then home; no physical position feedback");
    changeMode(IDLE,"servo boot command self-check complete; IR clear");
  }
}

Failure* failureFor(const char* uid){
  for(uint8_t i=0;i<12;++i)if(!strcmp(failures[i].uid,uid))return &failures[i];
  for(uint8_t i=0;i<12;++i)if(!failures[i].uid[0]){
    snprintf(failures[i].uid,sizeof(failures[i].uid),"%s",uid);
    failures[i].count=0;failures[i].blockUntil=0;return &failures[i];
  }
  return &failures[0]; // bounded replacement for toy-prototype table
}
int matchCard(const char *uid){for(int i=0;i<6;++i)if(people[i].uid[0]&&!strcmp(people[i].uid,uid))return i;return -1;}
void rememberFailure(const char *uid,const char *why,uint32_t now){
  deniedLedUntil=now+1200;
  ++denied;Failure *f=failureFor(uid);
  if(deadlineActive(f->blockUntil,now)){
    addEvent("CARD_BLOCKED",why,uid);setNotice("CARD BLOCKED");buzz(260);return;
  }
  if(f->count<3)f->count++;
  if(f->count>=3){f->blockUntil=now+UID_BLOCK_MS;f->count=0;
    addEvent("LOCKOUT_60S",why,uid);setNotice("BLOCKED 60 SEC",2500);
  }else{addEvent("ACCESS_DENIED",why,uid);setNotice("ACCESS DENIED");}
  buzz(200);
}
void grant(uint32_t now){
  approved++;authSession=true;exitPassage=false;exitOutsideSeen=false;exitCompleted=false;exitUnconfirmedNoted=false;exitCrossingWarning=false;
  entrySeen=false;clearSince=0;authorizationSince=now;
  timeoutFlag=false;anomalyFlag=false;lastDuration=0;lastZ=0;
  servoTarget=OPEN_ANGLE;addEvent("ACCESS_GRANTED",people[currentPerson].name);
  setNotice("ACCESS GRANTED",2200);buzz(85);
  changeMode(OPENING,"authorized and PIN accepted");
}
void grantExit(uint32_t now){
  approved++;exitGrants++;authSession=true;exitPassage=true;
  exitOutsideSeen=false;exitCompleted=false;exitUnconfirmedNoted=false;exitCrossingWarning=false;exitCrossingAt[0]=0;
  exitCrossingEpoch=0;exitCrossingMs=0;
  entrySeen=false;clearSince=0;authorizationSince=now;
  timeoutFlag=false;anomalyFlag=false;
  servoTarget=OPEN_ANGLE;
  addEvent("EXIT_RFID_GRANTED",people[currentPerson].name,people[currentPerson].uid);
  setNotice("EXIT ACCESS GRANTED",2100);buzz(85);
  Serial.print("[EXIT] ");Serial.print(people[currentPerson].name);
  Serial.println(" identified by RFID. Door opening; pass INSIDE -> OUTSIDE.");
  changeMode(OPENING,"registered active occupant requests exit");
}
void denyPIN(uint32_t now){
  deniedLedUntil=now+1200;
  denied++;wrongPIN++;setNotice("WRONG PIN",1300);buzz(250);
  addEvent("WRONG_PIN","PIN mismatch");
  Failure *f=failureFor(recentUID);
  if(f->count<3)f->count++;
  if(f->count>=3){f->blockUntil=now+UID_BLOCK_MS;f->count=0;
    addEvent("PIN_LOCKOUT","Three incorrect PINs");changeMode(WAIT_CARD,"lockout");
  }else if(wrongPIN>=3)changeMode(WAIT_CARD,"PIN attempts exhausted");
  enteredPin[0]=0;pinLen=0;pinSince=now;
}
void keyPress(char c,uint32_t now){
  // Dedicated wiring test: does not log in users or open the servo.
  // In test mode only, prints exact key labels to verify physical keypad ordering.
  if(keypadTestMode){
    Serial.print("[KEYTEST] Physical key: ");Serial.println(c);
    buzz(55);
    return;
  }
  if(rfidTestMode||mode!=WAIT_PIN)return; // Never authenticate while RFID raw-reader test is enabled.
  if(c=='*' || c=='B'){  // B is a reliable clear key if the fourth row (*) is disconnected.
    pinLen=0;enteredPin[0]=0;pinSince=now;
    setNotice("PIN CLEARED",850);buzz(60);return;
  }
  if(c=='A'){  // A replaces the non-working bottom-row # key.
    if(pinLen!=4){setNotice("ENTER FOUR DIGITS",1200);buzz(90);return;}
    // Grants/denials have their own distinct audible feedback.
    if(currentPerson>=0 && people[currentPerson].pin[0] && !strcmp(enteredPin,people[currentPerson].pin))grant(now);
    else denyPIN(now);
    return;
  }
  // Individual classroom-demo PINs have four digits; never echo them on displays.
  if(c>='0'&&c<='9' && pinLen<4){
    enteredPin[pinLen++]=c;enteredPin[pinLen]=0;
    pinSince=now; // Reset inactivity timeout after each digit.
    buzz(65);
    Serial.print("[PIN] Masked digit accepted (count=");Serial.print(pinLen);Serial.println(")");
    // Force both LCD rows to update immediately on the next refresh.
#if ENABLE_LCD_16X2
    lcdOldLine[1][0]=0;
#endif
  }
}
// In normal scan mode, keep inactive rows high-impedance with pullups;
// only one row is driven LOW at a time. All four columns have pullups.
// This reduces multi-key contention and makes scanning easier to troubleshoot.
char readKey(){
#if ENABLE_KEYPAD
  for(uint8_t r=0;r<4;++r)pinMode(ROWS[r],INPUT_PULLUP);
  for(uint8_t r=0;r<4;++r){
    digitalWrite(ROWS[r],LOW);
    pinMode(ROWS[r],OUTPUT);
    delayMicroseconds(35);
    for(uint8_t c=0;c<4;++c){
      if(digitalRead(COLS[c])==LOW){
        pinMode(ROWS[r],INPUT_PULLUP);
        return KEYMAP[r][c];
      }
    }
    pinMode(ROWS[r],INPUT_PULLUP);
  }
#endif
  return 0;
}
// KEYWIRE diagnostics: a pressed key electrically shorts EXACTLY two of the
// eight keypad conductors. Detect that pin pair without presuming R/C order.
// If 1..9 register but A/B/C/D or *0# do not, check D37 (C4) and D33 (R4).
void resetKeyPins(){
#if ENABLE_KEYPAD
  for(uint8_t k=0;k<8;++k)pinMode(KEYPINS[k],INPUT_PULLUP);
#endif
}
uint16_t readKeyWirePair(){
#if ENABLE_KEYPAD
  resetKeyPins();
  delayMicroseconds(25);
  for(uint8_t i=0;i<8;++i){
    digitalWrite(KEYPINS[i],LOW);
    pinMode(KEYPINS[i],OUTPUT);
    delayMicroseconds(35);
    for(uint8_t j=i+1;j<8;++j){
      if(digitalRead(KEYPINS[j])==LOW){
        pinMode(KEYPINS[i],INPUT_PULLUP);
        return (uint16_t)(i+1)*16U+(uint16_t)(j+1);
      }
    }
    pinMode(KEYPINS[i],INPUT_PULLUP);
  }
#endif
  return 0;
}
void keypadTick(uint32_t now){
#if ENABLE_KEYPAD
  if(keypadWireMode){
    static uint16_t raw=0,reported=0;
    static uint32_t changedAt=0;
    uint16_t pair=readKeyWirePair();
    if(pair!=raw){raw=pair;changedAt=now;}
    if(pair && pair!=reported && due(changedAt,45,now)){
      reported=pair;
      uint8_t ai=(uint8_t)(pair/16U)-1;
      uint8_t bi=(uint8_t)(pair%16U)-1;
      if(ai<8 && bi<8){
        Serial.print("[KEYWIRE] Press connects D");Serial.print(KEYPINS[ai]);
        Serial.print(" <-> D");Serial.println(KEYPINS[bi]);
      }
    }
    if(!pair)reported=0;
    return;  // KEYWIRE is diagnosis only; it never submits a PIN.
  }
  static char previous=0;static uint32_t changed=0;
  char key=readKey();
  if(key!=previous){previous=key;changed=now;}
  if(key && key!=lastKey && due(changed,32,now)){lastKey=key;keyPress(key,now);}
  if(!key)lastKey=0;
#endif
}
// An authorized card ACKNOWLEDGES the tamper alarm; it does not open the door.
// Unconfirmed entries remain unconfirmed, and inside attendance data is retained.
void clearTamperByRFID(int index,uint32_t now){
  tamperFlag=false;
  noInterrupts();tamperEdges=0;interrupts();
  windowCount=0;windowStart=now;
  buzzerUntil=0;buzzerQuiet();
  authSession=false;entrySeen=false;exitPassage=false;
  exitOutsideSeen=false;exitCompleted=false;exitUnconfirmedNoted=false;
  exitCrossingWarning=false;clearSince=0;
  currentPerson=-1;pinLen=0;enteredPin[0]=0;
  addEvent("TAMPER_CLEARED","Authorized RFID acknowledgement",people[index].uid);
  Serial.print("[TAMPER] RESOLVED by authorized RFID: ");
  Serial.println(people[index].name);
  setNotice("TAMPER CLEARED",3500);
  if(forcedAlarm){
    changeMode(ALARM,"separate forced-door alarm still active");
  }else if(!servoBootComplete){
    // Never bypass the startup command test when acknowledging a tamper event.
    changeMode(BOOT_HOLD,"tamper cleared; finish servo startup checks");
  }else if(doorwayBusy()){
    // Wait for both doorway IRs to clear before commanding the door closed.
    servoTarget=servoNow;
    changeMode(OPEN_HOLD,"authorized tamper reset; doorway occupied");
  }else{
    servoTarget=LOCK_ANGLE;
    changeMode(servoNow==LOCK_ANGLE?IDLE:CLOSING,
               "authorized tamper reset; return to closed angle");
  }
}

void checkRFID(uint32_t now){
  if(keypadWireMode && !tamperFlag)return; // Disable diagnostic gating during a breach.
  const bool normalScanState=(mode==WAIT_CARD||mode==WAIT_EXIT_CARD||mode==IDLE ||
                              (mode==ALARM && tamperFlag));
  // RFIDTEST bypasses door/IR-state GATING, but NEVER grants access.
  if(!rfidOK||(!rfidTestMode&&!normalScanState)||!due(lastRFID,RFID_COOLDOWN_MS,now))return;
  digitalWrite(SD_CS,HIGH); // RFID and microSD share SPI; ensure SD is deselected.
  if(!rfid.PICC_IsNewCardPresent()||!rfid.PICC_ReadCardSerial())return;
  lastRFID=now;scanCount++;
  char uid[21];uid[0]=0;size_t p=0;
  for(uint8_t i=0;i<rfid.uid.size&&p+2<sizeof(uid);++i){
    snprintf(uid+p,sizeof(uid)-p,"%02X",rfid.uid.uidByte[i]);p+=2;
  }
  // Tamper acknowledgement has priority over diagnostics and entry/exit.
  // No outside-IR trigger is required to present the authorized card.
  if(tamperFlag && mode==ALARM){
    snprintf(recentUID,sizeof(recentUID),"%s",uid);
    int user=matchCard(uid);
    if(user>=0 && people[user].allow){
      clearTamperByRFID(user,now);
    }else{
      Serial.print("[TAMPER] DENIED UID=");Serial.println(uid);
      addEvent("TAMPER_CLEAR_DENIED","Unknown or revoked RFID",uid);
      setNotice("AUTH RFID REQUIRED",2000);
    }
    rfid.PICC_HaltA();rfid.PCD_StopCrypto1();
    return;
  }
  // Diagnostic test reports card identity without altering authentication,
  // occupancy, servo state, permissions, or the current RFID session.
  if(rfidTestMode){
    Serial.print("[RFIDTEST] UID=");Serial.print(uid);
    Serial.print(" bytes=");Serial.print(rfid.uid.size);
    Serial.print(" current_state=");Serial.println(modeText());
    rfid.PICC_HaltA();rfid.PCD_StopCrypto1();
    return;
  }
  snprintf(recentUID,sizeof(recentUID),"%s",uid);
  Serial.print("[RFID] UID=");Serial.println(uid);
  int index=matchCard(uid);currentPerson=index;
#if ENABLE_LCD_16X2
  lcdLastIdentity=index;             // A named card -> LCD shows name + employee ID.
  lcdIdentityUntil=now+9000;        // Unknown card -> LCD shows UNKNOWN / ACCESS DENIED.
#endif
  if(index>=0){Serial.print("[RFID] NAME=");Serial.print(people[index].name);
    Serial.print(" EMP=");Serial.print(people[index].emp);
    Serial.print(" ROLE=");Serial.println(people[index].allow?"ALLOW":"DENY");}
  else {Serial.println("[RFID] UNASSIGNED credential; no user identity inferred");}
  Failure *f=failureFor(uid);
  if(mode==WAIT_EXIT_CARD){
    // EXIT never requires PIN in this classroom prototype, but it MUST use
    // the same authorized RFID card that was registered as being inside.
    if(deadlineActive(f->blockUntil,now)){
      rememberFailure(uid,"Credential temporarily blocked",now);
    }else if(index<0 || !people[index].allow){
      rememberFailure(uid,"Exit denied: unknown/unauthorized UID",now);
    }else if(!insideRecorded[index]){
      rememberFailure(uid,"Exit denied: no matching INSIDE entry",now);
      Serial.println("[EXIT] This card has no active entry. Enter normally first.");
    }else if(forcedAlarm){
      addEvent("EXIT_DENY_ALARM","Active alarm",uid);setNotice("ALARM ACTIVE");
    }else if(sensed(outer)){
      addEvent("EXIT_WAIT_CLEAR","Outside IR blocked; identity ambiguous",uid);
      setNotice("CLEAR OUTSIDE IR");
    }else{
      grantExit(now);
    }
  }else if(mode!=WAIT_CARD || !sensed(outer)){
    addEvent("SCAN_IGNORED","Outside IR must detect person",uid);
    setNotice("WAIT FOR OUTSIDE");
  }else if(deadlineActive(f->blockUntil,now)){
    denied++;deniedLedUntil=now+1200;addEvent("BLOCKED_UID","Credential blocked",uid);setNotice("CARD BLOCKED");buzz(150);
  }else if(index<0 || !people[index].allow){
    rememberFailure(uid,index<0?"Unregistered UID":"Revoked/denied UID",now);
  }else if(insideRecorded[index]){
    // Prevent a duplicate IN timestamp and confusing IN/OUT history.
    addEvent("ENTRY_ALREADY_INSIDE","Use INSIDE IR and scan card to EXIT",uid);
    setNotice("ALREADY INSIDE: EXIT");buzz(100);
  }else if(forcedAlarm){
    addEvent("DENY_ALARM","Reset alarm locally before accessing",uid);setNotice("ALARM ACTIVE");
  }else{
    wrongPIN=0;pinLen=0;enteredPin[0]=0;pinSince=now;
#if ENABLE_KEYPAD
    if(!people[index].pin[0]){
      rememberFailure(uid,"PIN not configured: deny by default",now);
      setNotice("PIN NOT SET");
    } else {
      addEvent("PIN_REQUIRED",people[index].name,uid);setNotice("ENTER PIN  A=OK");
      changeMode(WAIT_PIN,"RFID registered; PIN required");
    }
#else
    grant(now);
#endif
  }
  rfid.PICC_HaltA();rfid.PCD_StopCrypto1();
}
void fsm(uint32_t now){
  if(mode==BOOT_HOLD){safeServoInit(now);return;}
  if(mode==ALARM)return;
  // Reopen a closing toy door if either IR sensor sees someone nearby.
  if(mode==CLOSING&&doorwayBusy()){
    servoTarget=OPEN_ANGLE;clearSince=0;
    addEvent("IR_REOPEN","Person at doorway during closing");
    changeMode(OPENING,"doorway occupied; reopen");return;
  }
  if(authSession && !exitPassage && (mode==OPENING||mode==WAIT_ENTRY||mode==OPEN_HOLD)
     && servoNow==OPEN_ANGLE && sensed(inner) && !entrySeen){
    entrySeen=true;entryCount++;recordEntry(now);
  }
  // An RFID-approved exit is not complete merely because the door opened.
  // Record the OUTSIDE IR crossing time, but only COMMIT it once BOTH IRs clear.
  if(exitPassage && !exitCompleted && currentPerson>=0 &&
     (mode==OPENING||mode==OPEN_HOLD) && servoNow==OPEN_ANGLE && !exitOutsideSeen && sensed(outer)){
    if(rtcOK){clockNow=rtc.now();clockOK=clockTrusted&&clockNow.isValid()&&clockNow.year()>=2020;}
    stamp(exitCrossingAt,sizeof(exitCrossingAt));
    exitCrossingEpoch=clockOK?clockNow.unixtime():0;
    exitCrossingMs=now;
    exitOutsideSeen=true;
    addEvent("OUTSIDE_IR_EXIT","Exit RFID matched; awaiting doorway clear",people[currentPerson].uid);
    setNotice("CLEAR TO FINISH EXIT",1800);
  }
  if(exitPassage && exitOutsideSeen && !doorwayBusy())recordRFIDExit(now);
  if(exitPassage && !exitOutsideSeen && !exitCompleted && !exitCrossingWarning &&
     due(authorizationSince,EXIT_CROSSING_TIMEOUT_MS,now)){
    exitCrossingWarning=true;
    addEvent("EXIT_IR_TIMEOUT","RFID exit granted but no outside crossing recorded");
    setNotice("NO OUTSIDE CROSSING",2600);
  }
  if(authSession && doorOpened && !timeoutFlag && due(openSince,DOOR_TIMEOUT_MS,now)){
    timeoutFlag=true;observedTimeouts++;
    addEvent("DOOR_TIMEOUT","Door open for 15 seconds");
    setNotice("DOOR TIMEOUT",2800);buzz(400);
  }
  switch(mode){
    case IDLE:
      // Direction is defined before card scanning by where the person stands.
      if(sensed(inner) && !sensed(outer) && countInside()>0)
        changeMode(WAIT_EXIT_CARD,"inside person wants to exit; scan own RFID");
      else if(sensed(outer) && !sensed(inner))
        changeMode(WAIT_CARD,"outside person wants to enter");
      break;
    case WAIT_CARD:
      if(!sensed(outer)){
        if(sensed(inner) && countInside()>0)
          changeMode(WAIT_EXIT_CARD,"inside person requesting exit");
        else changeMode(IDLE,"outside person left");
      }
      break;
    case WAIT_EXIT_CARD:
      if(sensed(outer) && !sensed(inner))
        changeMode(WAIT_CARD,"outside arrival; entry takes priority");
      else if(!sensed(inner) && due(stateSince,EXIT_CARD_WAIT_MS,now))
        changeMode(IDLE,"exit card timeout");
      break;
    case WAIT_PIN:
      if(due(pinSince,PIN_TIMEOUT_MS,now)){
        addEvent("PIN_TIMEOUT");setNotice("PIN TIMEOUT");
        changeMode(WAIT_CARD,"PIN expired");
      }
      break;
    case OPENING:
      if(servoNow==OPEN_ANGLE)
        changeMode(exitPassage?OPEN_HOLD:(entrySeen?OPEN_HOLD:WAIT_ENTRY),
                   exitPassage?"exit door open, await outside crossing":"entry door open");
      break;
    case WAIT_ENTRY:
      if(entrySeen)changeMode(OPEN_HOLD,"inside IR confirms entry");
      else if(due(authorizationSince,NO_ENTRY_GRACE_MS,now))
        changeMode(OPEN_HOLD,"entry not yet detected; close when clear");
      break;
    case OPEN_HOLD:
      if(doorwayBusy())clearSince=0;
      else if(clearSince==0){
        clearSince=now;addEvent("CLEAR_COUNTDOWN","Both IR clear for 5 seconds");
      }else if(due(clearSince,CLEAR_CLOSE_MS,now)){
        if(!exitPassage && !entrySeen){
          addEvent("ENTRY_NOT_CONFIRMED","No inside IR detected after access grant");
          Serial.println("[ENTRY] NO crossing confirmed; no attendance ENTRY saved.");
        }
        if(exitPassage && !exitCompleted && !exitUnconfirmedNoted){
          exitUnconfirmedNoted=true;++exitUnconfirmed;
          addEvent("EXIT_NOT_CONFIRMED","No valid outside IR exit crossing; person stays INSIDE");
          Serial.println("[EXIT] NOT CONFIRMED: no attendance EXIT saved; user stays INSIDE.");
        }
        if(SERVO_DRIVES_DEMO_DOOR){
          servoTarget=LOCK_ANGLE;changeMode(CLOSING,"both IR clear 5 seconds");
        }else if(!ENABLE_DOOR_REED || reedClosed()){
          servoTarget=LOCK_ANGLE;changeMode(CLOSING,"latch relock; door confirmed closed");
        }else{
          changeMode(WAIT_REED,"physically close the door");setNotice("CLOSE DOOR FIRST",3500);
        }
      }
      break;
    case WAIT_REED:
      if(doorwayBusy()){
        clearSince=0;changeMode(OPEN_HOLD,"person near door; hold unlocked");
      }else if(reedClosed()){
        servoTarget=LOCK_ANGLE;changeMode(CLOSING,"door sensor closed");
      }
      break;
    case CLOSING:
      if(servoNow==LOCK_ANGLE){
        if(ENABLE_DOOR_REED && !reedClosed()){
          if(!closeWarningLogged && due(stateSince,3500,now)){
            closeWarningLogged=true;
            addEvent("CLOSE_UNVERIFIED","Servo shut angle but reed open");
            setNotice("CHECK DOOR",3000);
          }
          break;
        }
        closeWarningLogged=false;
        authSession=false;entrySeen=false;exitPassage=false;
        exitOutsideSeen=false;exitCompleted=false;exitUnconfirmedNoted=false;
        exitCrossingWarning=false;currentPerson=-1;clearSince=0;
        if(sensed(inner) && !sensed(outer) && countInside()>0)
          changeMode(WAIT_EXIT_CARD,"door shut; inside person waiting to exit");
        else changeMode(sensed(outer)?WAIT_CARD:IDLE,
          ENABLE_DOOR_REED?"door sensor closed":"close commanded (not physically verified)");
      }
      break;
    default:break;
  }
}
int secondsLeft(uint32_t now){
  if(mode!=OPEN_HOLD||!clearSince||doorwayBusy())return -1;
  uint32_t n=now-clearSince;
  return n>=CLEAR_CLOSE_MS?0:(int)((CLEAR_CLOSE_MS-n+999)/1000);
}
void smallScreen(uint32_t now){
  if(!smallOK)return;
  smallOLED.clearDisplay();smallOLED.setTextColor(SSD1306_WHITE);
  smallOLED.setTextSize(1);smallOLED.setCursor(0,0);smallOLED.print("ARM SECURE ACCESS");
  smallOLED.drawFastHLine(0,10,128,SSD1306_WHITE);
  smallOLED.setTextSize(2);smallOLED.setCursor(15,16);
  if(clockOK){
    char hm[9];snprintf(hm,sizeof(hm),"%02u:%02u:%02u",(unsigned)clockNow.hour(),(unsigned)clockNow.minute(),(unsigned)clockNow.second());
    smallOLED.print(hm);
  }else smallOLED.print("--:--:--");
  smallOLED.setTextSize(1);smallOLED.setCursor(28,40);
  if(tamperFlag){
    smallOLED.setCursor(0,40);smallOLED.print("TAMPER DETECTED");
    smallOLED.setCursor(0,54);smallOLED.print("SCAN AUTH RFID");
    smallOLED.display();return;
  }
  if(clockOK){char dt[12];snprintf(dt,sizeof(dt),"%02u/%02u/%04u",(unsigned)clockNow.day(),(unsigned)clockNow.month(),(unsigned)clockNow.year());smallOLED.print(dt);}
  else smallOLED.print(rtcOK?"SET RTC TIME":"RTC NOT FOUND");
  smallOLED.setCursor(0,54);
  smallOLED.print(deadlineActive(noticeUntil,now)?notice:modeText());
  smallOLED.display();
}
void largeScreen(uint32_t now){
#if ENABLE_LARGE_OLED
  if(!largeOK)return;
  largeOLED.clearDisplay();
  #if LARGE_OLED_IS_SH1106
  largeOLED.setTextColor(SH110X_WHITE);
  #else
  largeOLED.setTextColor(SSD1306_WHITE);
  #endif
  largeOLED.setTextSize(1);largeOLED.setCursor(0,0);largeOLED.print("SECURITY / DOOR");
  largeOLED.drawFastHLine(0,11,128,1);
  if(tamperFlag){
    largeOLED.setCursor(0,16);largeOLED.print("TAMPER DETECTED");
    largeOLED.setCursor(0,30);largeOLED.print("SCAN AUTHORIZED RFID");
    largeOLED.setCursor(0,43);largeOLED.print("ALARM LATCHED");
    largeOLED.setCursor(0,55);largeOLED.print("RFID ACK = RESET");
    largeOLED.display();return;
  }
  largeOLED.setCursor(0,16);largeOLED.print(modeText());
  // Show the actual DS3231 time (after SETTIME or Windows sync).
  if(clockOK){
    char liveHM[9];snprintf(liveHM,sizeof(liveHM),"%02u:%02u:%02u",
      (unsigned)clockNow.hour(),(unsigned)clockNow.minute(),(unsigned)clockNow.second());
    largeOLED.setCursor(78,16);largeOLED.print(liveHM);
  }
  largeOLED.setCursor(0,27);largeOLED.print("OUT:");largeOLED.print(sensed(outer)?"YES":"NO");
  largeOLED.print(" IN:");largeOLED.print(sensed(inner)?"YES":"NO");
  largeOLED.setCursor(0,38);
  int s=secondsLeft(now);
  if(s>=0){largeOLED.print("CLOSE IN ");largeOLED.print(s);largeOLED.print("s");}
  else if(mode==WAIT_PIN){
    largeOLED.print("PIN: ");
    for(uint8_t i=0;i<pinLen && i<4;++i)largeOLED.print('*');
    largeOLED.print("  A=OK");
  }
  else {largeOLED.print("SERVO:");largeOLED.print(servoNow);largeOLED.print("deg");}
  largeOLED.setCursor(0,49);largeOLED.print("INSIDE:");largeOLED.print(countInside());
  for(int i=0;i<6;++i)if(insideRecorded[i]){
    char hm[20];durationHMS(secondsInside(i,now),hm,sizeof(hm));
    largeOLED.print("  ");largeOLED.print(hm);break;
  }
  largeOLED.setCursor(0,57);largeOLED.print("RTC:");largeOLED.print(rtcOK?"OK":"ERR");
  largeOLED.print(" R:");largeOLED.print(riskScore());
  largeOLED.display();
#else
  (void)now;
#endif
}
#if ENABLE_LCD_16X2
void lcdLine(uint8_t row, const char *s){
  if(!lcdOK || row>1) return;
  char fitted[17];
  snprintf(fitted,sizeof(fitted),"%-16.16s",s?s:"");
  if(strcmp(lcdOldLine[row],fitted)==0) return;
  lcd.setCursor(0,row);
  lcd.print(fitted);
  strcpy(lcdOldLine[row],fitted);
}
void lcdIdentity(uint32_t now){
  if(!lcdOK) return;
  // Re-send both rows periodically: the backpack can ACK I2C while the
  // LCD controller lost its display contents in a momentary power dip.
  if(due(lastLCDRefresh,3500,now)){
    lastLCDRefresh=now;
    lcdOldLine[0][0]=0; lcdOldLine[1][0]=0;
  }
  // A tamper breach overrides identity/attendance views until an authorized scan.
  if(tamperFlag){
    lcdLine(0,"TAMPER DETECTED");
    lcdLine(1,"SCAN AUTH RFID");
    return;
  }
  // Highest-priority PIN entry view: show '*' only, never the actual digits.
  // Example LCD: "Vigneshwaran" / "PIN: **** A=OK".
  if(mode==WAIT_PIN){
    lcdLine(0, (currentPerson>=0 && currentPerson<6) ? people[currentPerson].name : "ENTER PASSWORD");
    char asterisks[5]={0};
    for(uint8_t i=0;i<pinLen && i<4;++i)asterisks[i]='*';
    char maskedLine[17];snprintf(maskedLine,sizeof(maskedLine),"PIN: %s A=OK",asterisks);
    lcdLine(1,maskedLine);
    return;
  }
  // One user and their timestamp temporarily take priority over the idle display.
  if(deadlineActive(lcdEventUntil,now)){
    lcdLine(0,lcdEventLine1);lcdLine(1,lcdEventLine2);return;
  }
  if(mode==WAIT_EXIT_CARD){
    lcdLine(0,"EXIT: SCAN RFID");
    char cnt[17];snprintf(cnt,sizeof(cnt),"INSIDE USERS: %d",countInside());
    lcdLine(1,cnt);return;
  }
  // Show identity after a scan, and throughout the corresponding authorized session.
  bool known=lcdLastIdentity>=0 && lcdLastIdentity<6;
  bool active=(known && authSession && currentPerson==lcdLastIdentity);
  bool waiting=deadlineActive(lcdIdentityUntil,now);
  if(known && (waiting||active)){
    lcdLine(0,people[lcdLastIdentity].name);
    if(active && exitPassage)lcdLine(1,"EXIT: WAIT OUT IR");
    else lcdLine(1,people[lcdLastIdentity].emp);
  } else if(lcdLastIdentity==-1 && waiting){
    lcdLine(0,"UNKNOWN RFID");
    lcdLine(1,"ACCESS DENIED");
  } else if(countInside()>0 && ((now/4500UL)%2U)==1U){
    int first=-1;for(int i=0;i<6;++i)if(insideRecorded[i]){first=i;break;}
    if(countInside()==1 && first>=0){
      lcdLine(0,people[first].name);
      char hms[22],label[17];durationHMS(secondsInside(first,now),hms,sizeof(hms));
      snprintf(label,sizeof(label),"STAY %s",hms);lcdLine(1,label);
    }else{
      char msg[17];snprintf(msg,sizeof(msg),"INSIDE USERS: %d",countInside());
      lcdLine(0,msg);lcdLine(1,"SCAN CARD TO EXIT");
    }
  } else {
    lcdLine(0,"ARM SMART ACCESS");
    if(clockOK && (mode==IDLE||mode==BOOT_HOLD)){
      char s[17];snprintf(s,sizeof(s),"%02u/%02u %02u:%02u:%02u",
        (unsigned)clockNow.day(),(unsigned)clockNow.month(),
        (unsigned)clockNow.hour(),(unsigned)clockNow.minute(),(unsigned)clockNow.second());
      lcdLine(1,s); // Show live RTC while waiting.
    }else lcdLine(1,modeText());
  }
}
#endif
void redraw(uint32_t now){
  if(!due(lastDisplay,250,now))return;
  lastDisplay=now;smallScreen(now);largeScreen(now);
#if ENABLE_LCD_16X2
  lcdIdentity(now);
#endif
}
void statPrint(uint32_t now){
  Serial.print("[STATUS] ");char t[22];stamp(t,sizeof(t));Serial.print(t);
  Serial.print(" state=");Serial.print(modeText());
  Serial.print(" out=");Serial.print(sensed(outer));Serial.print(" in=");Serial.print(sensed(inner));
  Serial.print(" reed_closed=");Serial.print(reedClosed());
  Serial.print(" room_motion=");Serial.print(indoorMotion());
  Serial.print(" servo=");Serial.print(servoNow);Serial.print("->");Serial.print(servoTarget);
  Serial.print(" close_in=");Serial.print(secondsLeft(now));
  Serial.print(" grants=");Serial.print(approved);Serial.print(" denies=");Serial.print(denied);
  Serial.print(" entries=");Serial.print(entryCount);
  Serial.print(" exit_rfid=");Serial.print(exitLogged);
  Serial.print(" exit_grants=");Serial.print(exitGrants);
  Serial.print(" inside_known=");Serial.print(countInside());
  Serial.print(" exits_unconfirmed=");Serial.print(exitUnconfirmed);
  Serial.print(" alarms=");Serial.print(intrusionCount);
  Serial.print(" tamper=");Serial.print(tamperFlag);
  Serial.print(" vib_th=");Serial.print(vibrationPulseThreshold);
  Serial.print(" vib_arm=");Serial.print(vibrationArmed);
  Serial.print(" servo_boot=");Serial.print(servoBootComplete);
  Serial.print(" timeouts=");Serial.print(observedTimeouts);
  Serial.print(" risk=");Serial.print(riskScore());
  Serial.print(" W_n=");Serial.print(wCount);Serial.print(" mean_s=");Serial.print(wMean,2);
  Serial.print(" M2=");Serial.print(wM2,2);Serial.print(" z=");Serial.print(entryStats.lastZ,2);
  Serial.print(" EX_n=");Serial.print(exitStats.n);
  Serial.print(" EX_mean_s=");Serial.print(exitStats.mean,2);
  Serial.print(" EX_z=");Serial.print(exitStats.lastZ,2);
  if(currentPerson>=0 && currentPerson<6){
    Serial.print(" user=");Serial.print(people[currentPerson].name);
    Serial.print(" emp=");Serial.print(people[currentPerson].emp);
  }
  Serial.print(" rtc=");Serial.print(rtcOK);
  Serial.print(" lcd=");
#if ENABLE_LCD_16X2
  Serial.print(lcdOK);
#else
  Serial.print(0);
#endif
  Serial.println();
}
void printUsers(){
  Serial.println("[USERS] Slot  Name  EmployeeID  Role  UID  PINconfigured");
  for(int i=0;i<6;++i){
    Serial.print(i+1);Serial.print(" | ");Serial.print(people[i].name);Serial.print(" | ");
    Serial.print(people[i].emp);Serial.print(" | ");Serial.print(people[i].allow?"AUTHORIZED":"DENIED");
    Serial.print(" | ");Serial.print(people[i].uid[0]?people[i].uid:"UNASSIGNED");
    Serial.print(" | ");Serial.println(people[i].pin[0]?"YES":"NO");
  }
  Serial.println("[RFID] Six scanned demo UIDs are built in.");
}
bool legalUID(const char *s){
  size_t n=strlen(s);if(!(n==8||n==14||n==20))return false;
  for(size_t i=0;i<n;++i)if(!isxdigit((unsigned char)s[i]))return false;
  return true;
}
bool legalPIN(const char *s){size_t n=strlen(s);if(n<4||n>8)return false;
  for(size_t i=0;i<n;++i)if(s[i]<'0'||s[i]>'9')return false;return true;}
void normalizeUID(char *s){for(;*s;s++)*s=toupper((unsigned char)*s);}
void loadCards(){
  if(!sdOK || !SD.exists(CFGNAME)){
    Serial.println("[PROVISION] No CARDS.CFG: using the 6 built-in scanned DEMO UID assignments");return;
  }
  File f=SD.open(CFGNAME,FILE_READ);
  if(!f)return;
  char line[72];uint8_t p=0;int loaded=0;
  while(f.available()){
    char ch=f.read();
    if(ch!='\n'&&ch!='\r'&&p<sizeof(line)-1){line[p++]=ch;continue;}
    if(p==0)continue;
    line[p]=0;p=0;
    int slot=0;char uid[22]={0},pin[12]={0};
    if(sscanf(line,"%d,%21[^,],%11s",&slot,uid,pin)==3 && slot>=1&&slot<=6){
      if(!strcmp(uid,"-"))uid[0]=0;
      if(!strcmp(pin,"-"))pin[0]=0;
      normalizeUID(uid);
      if((!uid[0]||legalUID(uid))&&(!pin[0]||legalPIN(pin))){
        snprintf(people[slot-1].uid,sizeof(people[slot-1].uid),"%s",uid);
        if(slot<=3)snprintf(people[slot-1].pin,sizeof(people[slot-1].pin),"%s",pin);
        loaded++;
      }
    }
  }
  f.close();Serial.print("[PROVISION] CARDS.CFG overrides scanned defaults; loaded slots: ");Serial.println(loaded);
}
// Load fixed individual demo PINs AFTER any optional saved-card configuration,
// so a stale CARDS.CFG can never revert or swap these intended assignments.
// Unknown and revoked users ALWAYS remain denied without a PIN.
void setAuthorizedDemoPins(){
  for(uint8_t i=0;i<6;++i){
    if(i<3 && people[i].allow){
      snprintf(people[i].pin,sizeof(people[i].pin),"%s",AUTHORIZED_USER_PINS[i]);
    } else {
      people[i].pin[0]=0;
    }
  }
  Serial.println("[PIN] Unique PINs configured for Vigneshwaran / Sri Harsha / Yogesh.");
  Serial.println("[PIN] Denied users remain blocked; keypad input is masked on displays.");
}
bool saveCards(){
  if(!sdOK)return false;
  digitalWrite(RFID_SS,HIGH);
  if(SD.exists(CFGNAME))SD.remove(CFGNAME);
  File f=SD.open(CFGNAME,FILE_WRITE);if(!f)return false;
  for(uint8_t i=0;i<6;++i){
    f.print(i+1);f.print(',');f.print(people[i].uid[0]?people[i].uid:"-");f.print(',');
    f.println(people[i].pin[0]?people[i].pin:"-");
  }
  f.close();enrollmentDirty=false;return true;
}
void printHex(const uint8_t* p,size_t n){
  for(size_t i=0;i<n;++i){if(p[i]<16)Serial.print('0');Serial.print(p[i],HEX);}Serial.println();
}
// NIST 128-bit zero-vector is used only for a fixed, PUBLIC self-test.
// DO NOT copy this key or nonce to real stored-user record encryption.
void cryptoBenchmark(){
  const uint8_t key[16]={0};const uint8_t iv[12]={0};
  const uint8_t plain[16]={0};
  const uint8_t expectedCT[16]={0x03,0x88,0xDA,0xCE,0x60,0xB6,0xA3,0x92,0xF3,0x28,0xC2,0xB9,0x71,0xB2,0xFE,0x78};
  const uint8_t expectedTag[16]={0xAB,0x6E,0x47,0xD4,0x2C,0xEC,0x13,0xBD,0xF5,0x3A,0x67,0xB2,0x12,0x57,0xBD,0xDF};
  uint8_t cipher[16],decrypted[16],tag[16];
  GCM<AES128> g;
  uint32_t a=micros();
  bool good=g.setKey(key,sizeof(key)) && g.setIV(iv,sizeof(iv));
  if(!good){Serial.println("[AES] Error setting parameters");return;}
  g.encrypt(cipher,plain,sizeof(plain));g.computeTag(tag,sizeof(tag));
  uint32_t encUS=micros()-a;
  Serial.print("[AES-128-GCM] CT: ");printHex(cipher,sizeof(cipher));
  Serial.print("[AES-128-GCM] TAG: ");printHex(tag,sizeof(tag));
  Serial.print("[AES] NIST encrypt vector: ");Serial.println(!memcmp(cipher,expectedCT,16)&&!memcmp(tag,expectedTag,16)?"PASS":"FAIL");
  a=micros();
  g.setKey(key,sizeof(key));g.setIV(iv,sizeof(iv));
  g.decrypt(decrypted,cipher,sizeof(cipher));
  bool authenticated=g.checkTag(tag,sizeof(tag));
  uint32_t decUS=micros()-a;
  Serial.print("[AES] Decrypt + tag verification: ");
  Serial.println(authenticated&&!memcmp(decrypted,plain,16)?"PASS":"FAIL");
  tag[0]^=0x01;
  g.setKey(key,sizeof(key));g.setIV(iv,sizeof(iv));g.decrypt(decrypted,cipher,16);
  bool changedRejected=!g.checkTag(tag,sizeof(tag));
  Serial.print("[AES] Tampered authentication tag rejected: ");
  Serial.println(changedRejected?"PASS":"FAIL");
  Serial.print("[AES] Encrypt_us=");Serial.print(encUS);
  Serial.print(" Decrypt_verify_us=");Serial.println(decUS);
  // Benchmark: no serial output inside timed region.
  a=micros();
  for(int i=0;i<50;i++){
    g.setKey(key,sizeof(key));g.setIV(iv,sizeof(iv));
    g.encrypt(cipher,plain,16);g.computeTag(tag,16);
  }
  Serial.print("[AES] Mean encrypt+tag us across 50 calls: ");Serial.println((micros()-a)/50.0,2);
  addEvent("AES_SELFTEST",changedRejected&&authenticated?"Known answer + authenticated decrypt PASS":"CRYPTO TEST FAILED");
}
void logTail(){
  if(!sdOK||mode!=IDLE||doorwayBusy()){Serial.println("[SD] LOGTAIL requires idle and both doorway IRs clear");return;}
  digitalWrite(RFID_SS,HIGH);File f=SD.open(LOGNAME,FILE_READ);
  if(!f){Serial.println("[SD] Could not open event file");return;}
  uint32_t count=0;while(f.available()&&count<5000){Serial.write(f.read());count++;}
  f.close();Serial.println();
  if(count>=5000)Serial.println("[SD] First 5000 bytes shown (bounded output)");
}
void auditCSV(){
  if(!sdOK||mode!=IDLE||doorwayBusy()) {Serial.println("[AUDIT] Requires idle and clear sensors");return;}
  digitalWrite(RFID_SS,HIGH);File f=SD.open(LOGNAME,FILE_READ);
  if(!f){Serial.println("[AUDIT] Could not read ACCESS.CSV");return;}
  uint32_t ok=0,bad=0;char line[310];size_t p=0;
  while(f.available()){
    char c=f.read();if(c=='\r')continue;
    if(c!='\n' && p<sizeof(line)-1){line[p++]=c;continue;}
    line[p]=0;p=0;
    if(line[0]==0||!strncmp(line,"seq,",4))continue;
    char *tagSeparator=strrchr(line,',');
    if(!tagSeparator){bad++;continue;}
    char tag[36];snprintf(tag,sizeof(tag),"%s",tagSeparator+1);
    *tagSeparator=0; // line now has payload + CRC32
#if ENABLE_HMAC_AUDIT
    char expected[33];hmacTag16(line,expected);
    if(strcmp(tag,expected)){bad++;continue;}
#endif
    char *crcSep=strrchr(line,',');
    if(!crcSep){bad++;continue;}
    uint32_t recorded=(uint32_t)strtoul(crcSep+1,NULL,16);
    *crcSep=0; // line now has original CSV payload
    if(crcText(line)==recorded)ok++;else bad++;
  }
  f.close();Serial.print("[AUDIT CRC32/HMAC] valid=");Serial.print(ok);
  Serial.print(" altered/corrupt=");Serial.println(bad);
  Serial.println("[AUDIT] Prototype key is public; no secure key storage or append-only protection.");
}

void command(char *s){
  if(!strcmp(s,"HELP")){
    Serial.println("STATUS USERS SCAN LCDTEST [1..6] LCDRESET LCDLIGHT ON/OFF RTC? TIME? CRYPTOTEST RESETALARM");
    Serial.println("KEYTEST ON/OFF: label test; KEYWIRE ON/OFF: keypad conductor test");
    Serial.println("RFIDCHECK: reread MFRC522 version; RFIDTEST ON/OFF: raw UID reader without door/IR gating");
    Serial.println("KEYPAD matrix: R1..R4=D30,D31,D32,D33; C1..C4=D34,D35,D36,D37");
    Serial.println("BUZZTEST | BUZZMODE ACTIVE / PASSIVE / LOW | LEDTEST");
    Serial.println("TAMPER: SW420 DO D28, 8 validated pulses/1.5s default; VIBSET 2..12; VIBSTATUS.");
    Serial.println("TAMPER latched; ONLY authorized RFID clears (NO PIN); RESETALARM blocked.");
    Serial.println("SERVOBOOT? status; SERVOHOME commands 0 deg while idle/IR clear (even if occupants logged).");
    Serial.println("SERVORESET runs 8-deg startup jog/home only when safely idle with NO occupants.");
    Serial.println("SETTIME YYYY-MM-DD HH:MM:SS  (LOCAL PC TIME, e.g. India IST)");
    Serial.println("PIN by RFID identity: Vigneshwaran=1111, Sri Harsha=1112, Yogesh=1113. A=SUBMIT, B/*=CLEAR.");
    Serial.println("To sync exact real time: close Serial Monitor and run Sync_RTC_Windows.cmd.");
    Serial.println("ENTRY: outside IR -> own RFID -> that person's PIN + A -> OPEN -> inside IR -> CLOSE");
    Serial.println("EXIT: inside IR -> same RFID (no PIN) -> OPEN -> outside IR -> both clear -> CLOSE");
    Serial.println("INSIDE: list inside users and live RTC stays; LASTEXIT: latest recorded stay");
    Serial.println("PASSAGESTATS: Welford + Z-score IR entry/exit timing (RAM); warmup 5, |z|>=3");
    Serial.println("SETUID 1 A3D87221 (RAM only until restart); personal PIN fixed by slot");
  }else if(!strcmp(s,"PASSAGESTATS")){
    Serial.println("[PASSAGE] RFID+PIN grant -> inside IR (ENTRY); exit RFID -> outside IR (EXIT)");
    Serial.println("[PASSAGE] Welford baseline excludes invalid and anomalous samples; RAM only");
    Serial.print("[PASSAGE] ENTRY N=");Serial.print(entryStats.n);
    Serial.print(" mean_s=");Serial.print(entryStats.mean,3);
    Serial.print(" M2=");Serial.print(entryStats.m2,3);
    Serial.print(" last_s=");Serial.print(entryStats.lastSeconds,3);
    Serial.print(" last_z=");Serial.print(entryStats.lastZ,3);
    Serial.print(" anomalies=");Serial.println(entryStats.anomalies);
    Serial.print("[PASSAGE] EXIT N=");Serial.print(exitStats.n);
    Serial.print(" mean_s=");Serial.print(exitStats.mean,3);
    Serial.print(" M2=");Serial.print(exitStats.m2,3);
    Serial.print(" last_s=");Serial.print(exitStats.lastSeconds,3);
    Serial.print(" last_z=");Serial.print(exitStats.lastZ,3);
    Serial.print(" anomalies=");Serial.println(exitStats.anomalies);
    Serial.println("[PASSAGE] Warning is advisory only; no claim of unauthorized entry.");
  }else if(!strcmp(s,"VIBSTATUS")){
    Serial.print("[VIB] D28 SW420 DIGITAL pulse threshold=");Serial.print(vibrationPulseThreshold);
    Serial.print(" pulses per ");Serial.print(VIBRATION_WINDOW_MS);
    Serial.print("ms; accepted-in-window=");Serial.print(windowCount);
    Serial.print(" armed=");Serial.print(vibrationArmed);
    Serial.print(" latched=");Serial.println(tamperFlag);
    Serial.println("[VIB] Sensitivity potentiometer on SW420 sets physical trigger level; VIBSET sets SOFTWARE pulse count.");
  }else if(!strncmp(s,"VIBSET ",7)){
    if(tamperFlag){Serial.println("[VIB] Denied during latched alarm: scan authorized RFID first.");return;}
    int requested=0;char extra='\0';
    if(sscanf(s+7,"%d %c",&requested,&extra)==1 && requested>=2 && requested<=12){
      vibrationPulseThreshold=(uint8_t)requested;
      noInterrupts();tamperEdges=0;interrupts();
      windowCount=0;windowStart=millis();
      Serial.print("[VIB] Pulse threshold now ");Serial.print(vibrationPulseThreshold);
      Serial.println(" active pulses per 1500ms (RAM only, reverts to 8 at reboot).");
    }else Serial.println("[VIB] Usage: VIBSET 2..12 (default 8; higher = fewer alarms).");
  }else if(!strcmp(s,"SERVOBOOT?")){
    Serial.print("[SERVO BOOT] stage=");Serial.print(servoBootStageName());
    Serial.print(" completed=");Serial.print(servoBootComplete);
    Serial.print(" attached=");Serial.print(servoAttached);
    Serial.print(" commanded=");Serial.print(servoNow);
    Serial.print(" target=");Serial.println(servoTarget);
    Serial.println("[SERVO BOOT] Open-loop command only; no position/current/feedback sensor installed.");
  }else if(!strcmp(s,"SERVOHOME")){
    // Reassert the initial/closed angle without a test jog. A person who has
    // already entered may remain in the attendance list (countInside()>0).
    // Only available when the access FSM is idle and the doorway is clear;
    // never override an active authentication, opening, exit or tamper alarm.
    if(mode!=IDLE || !servoBootComplete || authSession || doorwayBusy() ||
       tamperFlag || forcedAlarm){
      Serial.println("[SERVO HOME] Refused: wait for WAIT PERSON, IRs clear, no active access/alarm.");
    }else{
      // Servo.write() continuously reasserts the positional pulse after attach.
      // Reissue it even if software already reports servo=0->0: that value is
      // only a command and does not measure the MG996R physical position.
      if(!servoAttached){
        servo.attach(SERVO_PIN);
        servoAttached=true;
      }
      servoTarget=LOCK_ANGLE;
      servo.write(LOCK_ANGLE);
      servoNow=LOCK_ANGLE;
      lastServoStep=millis();
      Serial.print("[SERVO HOME] Initial position commanded: ");
      Serial.print(LOCK_ANGLE);
      Serial.println(" degrees. No physical feedback; visually inspect the unloaded servo.");
      Serial.println("[SERVO HOME] Occupancy/attendance records preserved.");
      addEvent("SERVO_HOME","Manual reassert closed servo angle; no feedback");
    }
  }else if(!strcmp(s,"SERVORESET")){
    // Only restart this moving boot check when there is no active person or alarm.
    if(mode!=IDLE || authSession || doorwayBusy() || tamperFlag || forcedAlarm || countInside()>0){
      Serial.println("[SERVO BOOT] SERVORESET refused: requires IDLE, clear IRs, no alarm, no occupants.");
    }else{
      servoBootComplete=false;
      vibrationArmed=false;
      servoBootReadyAt=0;
      noInterrupts();tamperEdges=0;interrupts();
      windowCount=0;windowStart=millis();
      servoBootStage=SERVO_BOOT_WAIT_CLEAR;
      servoBootStageAt=millis();
      changeMode(BOOT_HOLD,"manual servo jog/return-to-home command test");
      Serial.println("[SERVO BOOT] Rechecking servo commands, then returning to CLOSED.");
    }
  }else if(!strcmp(s,"INSIDE")){
    if(!clockOK)Serial.println("[INSIDE] WARNING RTC_INVALID; durations may be approximated");
    Serial.print("[INSIDE] Total currently inside: ");Serial.println(countInside());
    for(uint8_t i=0;i<6;++i)if(insideRecorded[i]){
      char pretty[24];durationHMS(secondsInside(i,millis()),pretty,sizeof(pretty));
      Serial.print("[INSIDE] ");Serial.print(people[i].name);
      Serial.print(" UID=");Serial.print(people[i].uid);
      Serial.print(" ENTRY=");Serial.print(enteredAt[i]);
      Serial.print(" ELAPSED=");Serial.println(pretty);
    }
  }else if(!strcmp(s,"LASTEXIT")){
    if(!lastExitName[0])Serial.println("[LASTEXIT] No exit recorded during this boot");
    else {
      char pretty[24];durationHMS(lastExitSeconds,pretty,sizeof(pretty));
      Serial.print("[LASTEXIT] ");Serial.print(lastExitName);
      Serial.print(" ENTERED=");Serial.print(lastExitIn);
      Serial.print(" EXITED=");Serial.print(lastExitOut);
      Serial.print(" STAY=");Serial.println(pretty);
    }
  }else if(!strcmp(s,"RFIDCHECK")){
    digitalWrite(SD_CS,HIGH);
    digitalWrite(RFID_SS,HIGH);
    rfid.PCD_Init();delay(50);
    byte v=rfid.PCD_ReadRegister(MFRC522::VersionReg);
    rfidOK=(v!=0x00&&v!=0xFF);
    Serial.print("[RFIDCHECK] Version=0x");Serial.print(v,HEX);
    Serial.print(" reader=");Serial.println(rfidOK?"OK":"FAIL");
    if(!rfidOK)Serial.println("[RFIDCHECK] Check 3.3V/GND, D10/SS, D9/RST, and Due main SPI header.");
  }else if(!strcmp(s,"RFIDTEST ON")){
    if(keypadWireMode){
      Serial.println("[RFIDTEST] First enter KEYWIRE OFF, then RFIDTEST ON.");
    }else if(tamperFlag){
      Serial.println("[RFIDTEST] Disabled during a tamper breach; scan authorized RFID normally.");
    }else if(mode==WAIT_PIN||mode==OPENING||mode==WAIT_ENTRY||mode==OPEN_HOLD||mode==CLOSING||mode==WAIT_REED){
      Serial.println("[RFIDTEST] Wait for idle/scan-ready state before enabling reader diagnostics.");
    }else{
      rfidTestMode=true;
      Serial.println("[RFIDTEST] ON: scan any card, even with both IR sensors clear.");
      Serial.println("[RFIDTEST] Authentication and door grants DISABLED for scans in this mode.");
      Serial.println("[RFIDTEST] Remove the card completely after each scan. RFIDTEST OFF restores normal scanning.");
    }
  }else if(!strcmp(s,"RFIDTEST OFF")){
    rfidTestMode=false;
    Serial.println("[RFIDTEST] OFF: normal RFID entry/exit state checks restored.");
  }else if(!strcmp(s,"KEYWIRE ON")){
    if(tamperFlag){Serial.println("[KEYWIRE] Blocked during tamper breach");return;}
    keypadWireMode=true;keypadTestMode=true;
    resetKeyPins();lastKey=0;
    Serial.println("[KEYWIRE] ON: push ONE physical key at a time; release between presses.");
    Serial.println("[KEYWIRE] Reports Dxx<->Dyy contacts regardless of ribbon order.");
    Serial.println("[KEYWIRE] Missing entire bottom row => check D33; missing A/B/C/D => check D37.");
    Serial.println("[KEYWIRE] Authentication disabled; type KEYWIRE OFF to exit.");
  }else if(!strcmp(s,"KEYWIRE OFF")){
    keypadWireMode=false;keypadTestMode=false;
    resetKeyPins();lastKey=0;
    Serial.println("[KEYWIRE] OFF. Standard 4x4 keypad and RFID+PIN active.");
  }else if(!strcmp(s,"KEYTEST ON")){
    if(tamperFlag){Serial.println("[KEYTEST] Blocked during tamper breach");return;}
    keypadWireMode=false;keypadTestMode=true;
    resetKeyPins();lastKey=0;
    Serial.println("[KEYTEST] ON: press all sixteen keys. Every key should print its logical label.");
    Serial.println("[KEYTEST] In authentication: A=submit 4 digits, B=clear (or *). C/D/# unused.");
    Serial.println("[KEYTEST] A submits PIN; B or * clears it ONLY in RFID PIN-entry mode.");
  }else if(!strcmp(s,"KEYTEST OFF")){
    keypadWireMode=false;keypadTestMode=false;
    resetKeyPins();lastKey=0;
    Serial.println("[KEYTEST] OFF. Normal RFID + PIN authentication enabled.");
  }else if(!strcmp(s,"BUZZTEST")){
    buzz(650);
    Serial.println("[BUZZTEST] Sound requested for 650ms at D26. If silent try BUZZMODE PASSIVE or LOW.");
  }else if(!strcmp(s,"BUZZMODE ACTIVE") || !strcmp(s,"BUZZMODE LOW") || !strcmp(s,"BUZZMODE PASSIVE")){
    buzzerQuiet();buzzerUntil=0;
    if(!strcmp(s,"BUZZMODE LOW"))buzzerMode=BUZZ_ACTIVE_LOW;
    else if(!strcmp(s,"BUZZMODE PASSIVE"))buzzerMode=BUZZ_PASSIVE;
    else buzzerMode=BUZZ_ACTIVE_HIGH;
    buzzerQuiet();
    Serial.print("[BUZZMODE] ");Serial.println(buzzerMode==BUZZ_ACTIVE_HIGH?"ACTIVE (high)":buzzerMode==BUZZ_ACTIVE_LOW?"ACTIVE (low)":"PASSIVE 2 kHz");
    Serial.println("[BUZZMODE] Now send BUZZTEST. Requires properly rated buzzer and driver.");
  }else if(!strcmp(s,"LEDTEST")){
    ledTestUntil=millis()+4000;
    Serial.println("[LEDTEST] Green/red LEDs blink alternately for four seconds, pins D24/D25.");
  }else if(!strcmp(s,"STATUS")){statPrint(millis());}
  else if(!strcmp(s,"USERS")){printUsers();}
  else if(!strcmp(s,"SCAN")){Serial.println("[SCAN] Present a registered card when outside IR detects a person");}
  else if(!strncmp(s,"LCDTEST",7)){
#if ENABLE_LCD_16X2
    int slot=1;
    if(s[7]==' ')slot=atoi(s+8);
    if(slot>=1 && slot<=6){
      lcdLastIdentity=slot-1; lcdIdentityUntil=millis()+12000;
      lcdOldLine[0][0]=0;lcdOldLine[1][0]=0;
      Serial.print("[LCDTEST] Showing identity slot ");Serial.print(slot);
      Serial.println(" for 12s. This is NOT authentication.");
    }else Serial.println("[LCDTEST] Use LCDTEST 1 through LCDTEST 6");
#else
    Serial.println("[LCDTEST] LCD disabled at compile time");
#endif
  }
  else if(!strcmp(s,"LCDRESET")){
#if ENABLE_LCD_16X2
    // Reinitialize the LCD and force diagnostic text regardless of prior cache.
    int rc=lcd.begin(16,2); lcdOK=(rc==0);
    Serial.print("[LCDRESET] begin status: "); Serial.println(rc);
    if(lcdOK){
      if(lcdLightWanted)lcd.backlight();else lcd.noBacklight();
      lcd.display();lcd.clear();
      lcdOldLine[0][0]=0;lcdOldLine[1][0]=0;
      lcdLastIdentity=0;lcdIdentityUntil=millis()+15000;
      lcdLine(0,"VIGNESHWARAN");
      lcdLine(1,"CB.EN.P2EBS26030");
      Serial.println("[LCDRESET] Sent two LCD test rows at address 0x27");
    }
#else
    Serial.println("[LCDRESET] LCD feature disabled");
#endif
  }
  else if(!strcmp(s,"LCDLIGHT ON") || !strcmp(s,"LCDLIGHT OFF")){
#if ENABLE_LCD_16X2
    lcdLightWanted=!strcmp(s,"LCDLIGHT ON");
    if(lcdOK){if(lcdLightWanted)lcd.backlight();else lcd.noBacklight();}
    Serial.print("[LCD] Backlight command = ");Serial.println(lcdLightWanted?"ON":"OFF");
    Serial.println("[LCD] If no physical change, verify LCD LED jumper and 5V module-rated power.");
#else
    Serial.println("[LCD] Disabled");
#endif
  }
  else if(!strcmp(s,"TIME?")){
    if(rtcOK){
      clockNow=rtc.now();
      clockOK=clockTrusted && clockNow.isValid() && clockNow.year()>=2020 && clockNow.year()<2100;
      char t[22];stamp(t,sizeof(t));
      Serial.print("[RTC TIME] ");Serial.print(t);
      Serial.print(" trusted=");Serial.print(clockTrusted);
      Serial.print(" lostPower=");Serial.println(rtc.lostPower());
    }else Serial.println("[RTC TIME] RTC unavailable; check SDA D20 / SCL D21");
  }
  else if(!strcmp(s,"RTC?")){
    Serial.print("[I2C Wire] OLED 0x3C=");Serial.print(probe(Wire,SMALL_I2C));
    Serial.print(" EEPROM 0x57=");Serial.print(probe(Wire,0x57));
    Serial.print(" RTC 0x68=");Serial.print(probe(Wire,RTC_I2C));
    Serial.print(" LCD 0x27=");Serial.print(probe(Wire,0x27));
    Serial.print(" LCD 0x3F=");Serial.println(probe(Wire,0x3F));
#if ENABLE_LARGE_OLED
    Serial.print("[Wire1] OLED 0x3C=");Serial.println(probe(Wire1,LARGE_I2C));
#endif
  }  else if(!strcmp(s,"CRYPTOTEST")){cryptoBenchmark();}
  else if(!strcmp(s,"RESETALARM")){
    if(tamperFlag){
      Serial.println("[TAMPER] RESETALARM denied. Scan an AUTHORIZED RFID to resolve breach.");
      return;
    }
    if(!doorwayBusy()&&(!ENABLE_DOOR_REED||reedClosed())){
      forcedAlarm=false;tamperFlag=false;timeoutFlag=false;anomalyFlag=false;
      authSession=false;servoTarget=LOCK_ANGLE;
      addEvent("ALARM_RESET","Local USB acknowledgement");
      changeMode(servoBootComplete?IDLE:BOOT_HOLD,
                 servoBootComplete?"alarm acknowledged":"alarm acknowledged; finish servo startup");
    }else Serial.println("[ALARM] Cannot reset until doorway clear and door closed");
  }else if(!strncmp(s,"SETTIME ",8)){
    // Times are provided in the PC's LOCAL time (IST for India).
    // Use included Sync_RTC_Windows.cmd to transmit current time without Python.
    // Neither build time nor millis() is an accurate replacement for RTC sync.
    int y,mo,d,h,mi,se;
    if(sscanf(s+8,"%d-%d-%d %d:%d:%d",&y,&mo,&d,&h,&mi,&se)==6 &&
       y>=2020&&y<=2099&&mo>=1&&mo<=12&&d>=1&&d<=31&&h>=0&&h<24&&mi>=0&&mi<60&&se>=0&&se<60 && rtcOK &&
       DateTime(y,mo,d,h,mi,se).isValid() &&
       DateTime(y,mo,d,h,mi,se).year()==(uint16_t)y &&
       DateTime(y,mo,d,h,mi,se).month()==(uint8_t)mo &&
       DateTime(y,mo,d,h,mi,se).day()==(uint8_t)d){
      rtc.adjust(DateTime(y,mo,d,h,mi,se));clockNow=rtc.now();clockTrusted=true;clockOK=true;
      lastClock=millis();
      Serial.println("[RTC] SETTIME applied");addEvent("SETTIME","RTC clock set via local serial");
    }else Serial.println("[RTC] Invalid value/RTC unavailable");
  }else if(!strncmp(s,"SETUID ",7)){
    if(tamperFlag){Serial.println("[CONFIG] No UID updates during a tamper breach");return;}
#if SERIAL_PROVISION
    int slot;char uid[24];
    if(sscanf(s+7,"%d %23s",&slot,uid)==2&&slot>=1&&slot<=6){
      normalizeUID(uid);
      if(!legalUID(uid)){Serial.println("[CONFIG] UID must be 8/14/20 hex chars");return;}
      for(int i=0;i<6;++i)if(i!=slot-1&&!strcmp(people[i].uid,uid)){
        Serial.println("[CONFIG] Duplicate UID rejected");return;
      }
      snprintf(people[slot-1].uid,sizeof(people[slot-1].uid),"%s",uid);
      enrollmentDirty=true;Serial.println("[CONFIG] UID set in RAM (until restart)");
    }else Serial.println("[CONFIG] Usage SETUID 1 A3D87221");
#else
    Serial.println("[CONFIG] Provisioning disabled");
#endif
  }else if(!strncmp(s,"SETPIN ",7)){
    Serial.println("[CONFIG] SETPIN disabled: fixed individual PINs by slot (1111/1112/1113).");
  }else Serial.println("[SERIAL] Unknown command. HELP lists supported commands.");
}
void pollSerial(){
  while(Serial.available()){
    char c=Serial.read();
    if(c=='\r')continue;
    if(c=='\n'){
      if(serialLen){serialLine[serialLen]=0;command(serialLine);serialLen=0;}
    }else if(serialLen<sizeof(serialLine)-1)serialLine[serialLen++]=c;
  }
}
void setup(){
  Serial.begin(115200);delay(1200);
  Serial.println("\n=== ARDUINO DUE / ARM SMART ACCESS / LEVEL 1+2+3 ===");
  Serial.println("3.3V GPIO ONLY. Prototype, no human-safe door claims.");
  pinMode(RFID_SS,OUTPUT);digitalWrite(RFID_SS,HIGH);
  pinMode(SD_CS,OUTPUT);digitalWrite(SD_CS,HIGH);
  pinMode(GREEN_LED,OUTPUT);pinMode(RED_LED,OUTPUT);pinMode(BUZZER,OUTPUT);
  buzzerQuiet();
  digitalWrite(GREEN_LED,LOW);digitalWrite(RED_LED,LOW);
  beginDebounced(outer);beginDebounced(inner);
#if ENABLE_DOOR_REED
  beginDebounced(reed);
#endif
#if ENABLE_ROOM_PIR
  beginDebounced(pir);
#endif
#if ENABLE_RADAR
  beginDebounced(radar);
#endif
#if ENABLE_TAMPER
  pinMode(TAMPER_PIN,INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TAMPER_PIN),tamperIRQ,CHANGE);
#endif
#if ENABLE_KEYPAD
  resetKeyPins();
#endif
  Wire.begin();Wire.setClock(100000);
#if ENABLE_LARGE_OLED
  Wire1.begin();Wire1.setClock(100000);
#endif
  Serial.print("[I2C] RTC 0x68: ");Serial.println(probe(Wire,RTC_I2C)?"ACK":"NO ACK");
  rtcOK=rtc.begin(&Wire);
  if(rtcOK){clockNow=rtc.now();clockTrusted=!rtc.lostPower();clockOK=clockTrusted&&clockNow.isValid() && clockNow.year()>=2020&&clockNow.year()<2100;
    if(rtc.lostPower())Serial.println("[RTC] LOST POWER - use SETTIME; not setting fictitious date automatically");
  }
  Serial.print("[RTC] ");Serial.println(rtcOK?"DETECTED":"NOT AVAILABLE");
  smallOK=smallOLED.begin(SSD1306_SWITCHCAPVCC,SMALL_I2C);
#if ENABLE_LARGE_OLED
  #if LARGE_OLED_IS_SH1106
  largeOK=largeOLED.begin(LARGE_I2C,true);
  #else
  largeOK=largeOLED.begin(SSD1306_SWITCHCAPVCC,LARGE_I2C);
  #endif
#endif
#if ENABLE_LCD_16X2
  Serial.print("[I2C] LCD candidate 0x27: ");Serial.println(probe(Wire,0x27)?"ACK":"NO ACK");
  Serial.print("[I2C] LCD candidate 0x3F: ");Serial.println(probe(Wire,0x3F)?"ACK":"NO ACK");
  int lcdStatus=lcd.begin(16,2);
  lcdOK=(lcdStatus==0);
  Serial.print("[LCD] hd44780 init: ");Serial.println(lcdOK?"OK":"FAIL");
  if(lcdOK){
    if(lcdLightWanted)lcd.backlight();else lcd.noBacklight();
    lcd.display();lcd.clear();
    lcdOldLine[0][0]=0;lcdOldLine[1][0]=0;
    lcdLine(0,"LCD ONLINE");
    lcdLine(1,"RTC & RFID TEST");
    Serial.println("[LCD] Initial text written at 0x27");
  }
#endif
  SPI.begin();delay(40);
  rfid.PCD_Init();delay(70);
  byte version=rfid.PCD_ReadRegister(MFRC522::VersionReg);
  rfidOK=version!=0x00 && version!=0xFF;
  Serial.print("[RFID] Version=0x");Serial.print(version,HEX);Serial.print(" reader=");Serial.println(rfidOK?"OK":"FAIL");
  setAuthorizedDemoPins();
  if(!rtcOK)Serial.println("[RTC] See RTC? and I2C scanner if not detected");
  printUsers();
  Serial.println("[SETUP] Startup check: both IRs clear -> command servo home -> jog 8 deg -> home.");
  Serial.println("[SETUP] SERVOHOME reissues the closed/home 0-deg command when idle and IR clear.");
  Serial.println("[SETUP] Servo position is NOT physically verified; inspect mechanism before energizing.");
  Serial.println("[VIB] SW420 active-pulse default threshold=8 per 1500ms; VIBSET 2..12.");
  Serial.println("[PASSAGE] IR Welford + Z-score active: ENTRY grant->inside; EXIT grant->outside.");
  Serial.println("[PASSAGE] PASSAGESTATS shows separate RAM-only baselines; min 5 samples, |z|>=3.");
  Serial.println("[SETUP] Type HELP in Serial Monitor (115200, Newline).");
  addEvent("BOOT","Full integrated demonstration startup");
  setNotice("WAIT FOR CLEAR",2500);
}
void loop(){
  uint32_t now=millis();
  pollSerial();timestampTick(now);
  IRs(now);tamperCheck(now);
  keypadTick(now);checkRFID(now);
  fsm(now);motorTick(now);
  buzzerTick(now);
  bool ledTesting=deadlineActive(ledTestUntil,now);
  if(ledTesting){
    // Alternating blink; no effect on access decision.
    bool phase=((now/250UL)&1U)!=0;
    digitalWrite(GREEN_LED,phase?HIGH:LOW);
    digitalWrite(RED_LED,phase?LOW:HIGH);
  }else{
    digitalWrite(GREEN_LED,authSession && !forcedAlarm ? HIGH:LOW);
    digitalWrite(RED_LED,(forcedAlarm||tamperFlag||timeoutFlag||deadlineActive(deniedLedUntil,now))?HIGH:LOW);
  }
#if ENABLE_LCD_16X2
  if(lcdOK && lcdLightWanted && due(lastLightRestore,10000,now)){
    lastLightRestore=now;lcd.backlight(); // Restore commanded ON after a short I2C glitch.
  }
#endif
  redraw(now);
  if(due(lastStatus,STATUS_MS,now)){lastStatus=now;statPrint(now);}
}
