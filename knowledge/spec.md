# ClinicBook: functional specification (sample client project for the Sensai demo, made up)

## 1. Overview

ClinicBook lets patients book, move and cancel appointments with a clinic online. Staff manage the agenda from a separate back office.

## 2. Accounts

Patients sign up with an email address and a password of at least 12 characters, including one digit.
An account is locked for 15 minutes after 5 failed logins in a row.

## 3. Password reset

A patient who forgot their password asks for a reset link from the login page.
The link is sent by email, works only once, and expires after 30 minutes.
After a reset, every open session of that account is logged out.
Staff accounts cannot reset their password themselves: an administrator does it from the back office.

## 4. Appointments

An appointment can be moved or cancelled up to 24 hours before it starts.
Inside 24 hours, the patient must call the clinic.
Each patient can hold at most 3 future appointments.

## 5. Notifications

A reminder is sent by email 48 hours and 2 hours before each appointment.
Patients can turn off the 2-hour reminder, not the 48-hour one.

## 6. Data

Medical notes are never shown in the patient app and never sent by email.
Appointment history is kept for 5 years, then deleted.
