# Low Carbon London smart meter dataset

Source: UK Power Networks, "SmartMeter Energy Consumption Data in London Households" (Low Carbon London project), published on the London Datastore under a Creative Commons Attribution license. Kaggle mirror: "Smart meters in London" (jeanmidev).

## What the dataset contains

The dataset is a sample of 5,567 London households. Readings were taken every half hour between November 2011 and February 2014, in kilowatt-hours per half hour. The full collection has about 167 million rows.

The Kaggle daily_dataset used in this project aggregates the half-hourly readings per household per day. It gives the daily energy sum, mean, median, max, min, standard deviation, and the number of readings. A complete day has 48 readings, so a count below 48 means missing half-hours.

## Tariff trial in the data

About 1,100 households took part in a dynamic Time of Use (dToU) tariff trial. They received day-ahead price signals through the smart meter display or text message. There were three price levels: high at 67.20 pence per kWh, low at 3.99 pence per kWh, and standard at 11.76 pence per kWh.

The remaining households, about 4,500, paid a flat rate of 14.228 pence per kWh. Households on the dynamic tariff could change their consumption in response to prices, so a change in their daily energy is not always a fault.

# Interpretation notes for this project

This project uses the London households as stand-in meters and assigns them to Jordanian regions (localization). The values are real London measurements, the region names are assigned for the demo.

A sudden and lasting drop of a household's daily energy compared with its neighbors can mean a meter fault, an empty house, or a real change in use. A sudden lasting rise can mean a new load, such as an electric vehicle charger, or a meter fault. Compare the household with its own history and with neighbors before blaming a fault.

A day with fewer than 48 readings is a data quality problem, not a consumption change. Check the reading count first.