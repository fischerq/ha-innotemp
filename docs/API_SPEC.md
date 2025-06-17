# Innotemp Heating Controller API Specification

This document details the API for the Innotemp heating controller, based on analysis of its web interface communication. This API uses standard HTTP requests and Server-Sent Events (SSE).

## Authentication and Session Management

Authentication is required to establish a session and obtain a session cookie (`PHPSESSID`) that must be sent with all subsequent requests.

*   **Endpoint:** `/inc/groups.read.php`
*   **Method:** `POST`
*   **Headers:**
    *   `Content-Type: application/x-www-form-urlencoded`
*   **Request Body:** URL-encoded username and password.
    *   `un=YOUR_USERNAME&pw=YOUR_PASSWORD`
*   **Success Response:**
    *   HTTP Status: `200 OK`
    *   Body: JSON confirming success.
    *   Headers: `Set-Cookie` header containing the `PHPSESSID`.
*   **Action:** Extract the `PHPSESSID` from the `Set-Cookie` header and include it in the `Cookie` header of all subsequent requests. Sessions can time out; implement logic to re-authenticate and retry requests upon session failure.

## Fetching Configuration Data

This endpoint provides a complete mapping of all available parameters, including their IDs, room IDs, labels, and types. It should be called once after successful authentication.

*   **Endpoint:** `/inc/roomconf.read.php`
*   **Method:** `POST`
*   **Request Body:**
    *   `un=YOUR_USERNAME&pw=YOUR_PASSWORD&date_string=0` (Sending username and password again seems redundant based on session, but observed in analysis.)
*   **Action:** Parse and store the returned JSON data. This serves as the "data dictionary" for the system.

## Real-time Data via Server-Sent Events (SSE)

Real-time data updates are pushed from the server using SSE. This involves two steps: fetching signal names and then connecting to the event stream.

### Get Signal Names

This call retrieves the names associated with the data points sent via the SSE stream.

*   **Endpoint:** `/inc/live_signal.read.php`
*   **Method:** `POST`
*   **Request Body:**
    *   `init=1`
*   **Action:** Store the returned JSON array of signal names.

### Connect to the Event Stream

Establish a persistent connection to this endpoint to receive real-time data.

*   **Endpoint:** `/inc/live_signal.read.SSE.php`
*   **Method:** `GET`
*   **Action:** Keep this connection open. Received SSE messages will contain a `data` field which is a comma-separated string of values. Map these values to the signal names obtained in the previous step based on their index (position). Implement reconnection logic if the stream is interrupted.

## Sending Commands

This endpoint is used to change the value of controllable parameters.

*   **Endpoint:** `/inc/value.save.php`
*   **Method:** `POST`
*   **Request Body:** URL-encoded parameters.
    *   `room_id`: The ID of the room the parameter belongs to.
    *   `param`: The cryptic parameter ID (obtained from the configuration data).
    *   `val_new`: The new value to set.
    *   `val_prev`: The previous value of the parameter (observed in requests, purpose might be for optimistic locking or logging).
*   **Example Request Body (Turning Heating Rod ON):**
    *   `room_id=3&param=003_e17par02_gui001out1&val_new=1&val_prev=0`

## API Request Sequence: A Complete Flow

A robust integration should follow a sequence similar to this:

1.  **Login:** `POST` to `/inc/groups.read.php` with credentials. Capture and store the `PHPSESSID` cookie.
2.  **Fetch Configuration:** `POST` to `/inc/roomconf.read.php` to get the data dictionary.
3.  **Get Signal Names:** `POST` to `/inc/live_signal.read.php` with `init=1`.
4.  **Connect to SSE Stream:** `GET` to `/inc/live_signal.read.SSE.php` and maintain the connection for real-time updates.
5.  **Perform API Calls:** For fetching specific data (beyond SSE) or sending commands, make `POST` requests to the relevant endpoints, including the `PHPSESSID` cookie.
6.  **Handle Session Timeout:** If any API call (other than the initial login) fails in a way indicative of a session timeout (e.g., specific error code or response), return to Step 1 to re-authenticate and then retry the failed call.
7.  **Handle SSE Disconnection:** If the SSE stream is lost, re-authenticate (Step 1) and re-establish the stream (Step 3).

## Selected Parameter Mappings

Based on the analysis, here are examples of how some cryptic parameter IDs map to meaningful controls and sensors:

*   **Control: Switch (Type ONOFFAUTO)**
    *   **Values:** `0` = OFF, `1` = ON, `2` = AUTO
    *   `003_e17par02_gui001out1`: Heizstab Normal Heizen (Heating Rod Normal Heating)
*   **Sensor: Read-only**
    *   `001_d_display002inp1`: Pufferspeicher Oben (°C) (Buffer Storage Top)
    *   `003_d_display001inp4`: Batterie SOC (%) (Battery State of Charge)