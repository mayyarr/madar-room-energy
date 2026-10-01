# ACN-Data: EV charging sessions dataset

Source: Z. Lee, T. Li, S. Low, "ACN-Data: Analysis and Applications of an Open EV Charging Dataset", ACM e-Energy 2019, and the ACN-Data website (ev.caltech.edu/dataset), Caltech.

## What the dataset contains

ACN-Data is a public dataset of workplace electric vehicle charging sessions from the Adaptive Charging Network in California. It has over 30,000 sessions and is updated daily. Two sites are described in the paper: Caltech, with 54 stations in a public parking garage, and JPL, with 52 stations in a restricted garage for employees only. JPL is the typical workplace charging case.

A session is recorded automatically when a car connects. Each session has connection time, done charging time, disconnect time, kilowatt-hours delivered, station ID, and optional user inputs. Timestamps from the API are in UTC (GMT). The website warns that data downloaded through its web interface before 10 October 2019 has timestamps shifted by 7 to 8 hours, so this project uses only later sessions.

## User inputs

Drivers can scan a QR code and enter their expected departure time and requested energy. These are called claimed sessions. Sessions without input are unclaimed, so the userInputs field can be empty. The requested energy (kWhRequested) and the available time (minutesAvailable) come from the driver and are often inaccurate. The paper shows that user input predicts departure worse than statistical models.

A session can end with less energy than requested. The car may be full, the driver may leave early, or the charger may limit the power.

## Workplace charging patterns

On weekdays there is a strong morning arrival peak around 8 AM and departures around 5 to 6 PM. At JPL weekend use is minimal. Because of this weekday pattern, charging loads must be compared over whole weeks, not single days.

A charging fee of 0.12 dollars per kWh reduced the number of daily sessions at Caltech significantly, so price changes can shift charging demand.

# Interpretation notes for this project

This project uses the JPL sessions as stand-in chargers and assigns them to Jordanian regions (localization). The sessions are real, the region names are assigned for the demo.

A long connection with almost no energy delivered has two possible causes: the car was already full, or the charger is faulty. One such session proves nothing. A charger that repeatedly delivers almost nothing while other chargers deliver normally is a stronger sign of a fault.

A charger that was almost idle for weeks and then starts delivering energy adds load. If that added load is close in size to a rise seen on a nearby meter, charging is a plausible explanation for the rise.

Days with no sessions on many chargers at once are usually holidays or data gaps, not charger faults. The data is from California, so US holidays explain empty weekdays.