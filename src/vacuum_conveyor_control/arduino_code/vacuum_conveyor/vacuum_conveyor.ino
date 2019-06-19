/*
 *  Vacuum control
 *  Value  Vacuum Generator  Vacuum Breaker
 *      0                 0               0
 *      1                 0               1
 *      2                 1               0
 *      3                 1               1
 */

#define VACUUM_GENERATOR  12
#define VACUUM_BREAKER     8
#define CONVRYOR_CONTROL   4
#define PNEUMATIC_CYLINDER 7 

void setup() {
  // put your setup code here, to run once:
  Serial.begin(115200);
  pinMode(VACUUM_GENERATOR, OUTPUT);
  pinMode(VACUUM_BREAKER, OUTPUT);
  pinMode(CONVRYOR_CONTROL, OUTPUT);
  pinMode(PNEUMATIC_CYLINDER, OUTPUT);
}

void loop() {
  // put your main code here, to run repeatedly:
  if(Serial.available()){
    char command_type = Serial.read();
    if(command_type == 'v'){
      int command = Serial.read() - '0';
      switch(command){
        case 0:
          digitalWrite(VACUUM_GENERATOR, LOW);
          digitalWrite(VACUUM_BREAKER, LOW);
          //Serial.println("v: 0");
          break;
        case 1:
          digitalWrite(VACUUM_GENERATOR, LOW);
          digitalWrite(VACUUM_BREAKER, HIGH);
          //Serial.println("v: 1");
          break;
        case 2:
          digitalWrite(VACUUM_GENERATOR, HIGH);
          digitalWrite(VACUUM_BREAKER, LOW);
          //Serial.println("v: 2");
          break;
        case 3:
          digitalWrite(VACUUM_GENERATOR, HIGH);
          digitalWrite(VACUUM_BREAKER, HIGH);
          //Serial.println("v: 3");
          break;
      }
    }
    else if(command_type == 'c'){
      digitalWrite(CONVRYOR_CONTROL, HIGH);
      delay(100);
      digitalWrite(CONVRYOR_CONTROL, LOW);
      //Serial.println("c: 1");
    }
    else if(command_type == 'p'){
      int command = Serial.read() - '0';
      switch(command){
        case 0:
          digitalWrite(PNEUMATIC_CYLINDER, LOW);
          break;
        case 1:
          digitalWrite(PNEUMATIC_CYLINDER, HIGH);
          break;
      }
    }
  }
  delay(1);
}
