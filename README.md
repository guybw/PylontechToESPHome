# PylontechToESPHome
This project allows you to monitor Pylontech US2000/3000/5000 via ESPHome into Home Assistant.

This project allows you to read infomation from a US2000/US3000/US5000 and display it in ESPHome. I wrote this as I wanted to be able to use an ESP8266 and have it supported in ESPHome.

I've heavily relied on this work https://github.com/irekzielinski/Pylontech-Battery-Monitoring/blob/master/README.md


If you are in the UK I have a small batch of boards including the cable, custom Pylontech shield a ESP8266 and a custom case for sale for £20. Drop me an email at guybw at hotmail dOt com.


If you get anything working please let me know by raising an issue.


# Issues

Does your battery not return any data?
It's likely you need to send the HEX string:
7E 32 30 30 31 34 36 38 32 43 30 30 34 38 35 32 30 46 43 43 33 0D
as a speed of 1200 which then allows this to work. I'm sure someone can script this but right now, I do it via a USB to serial cable if the batteries get turned off.

pwrsys command doesn't work?

Press the "Login debug" button and that will enable debug and then the pwrsys command will work.
