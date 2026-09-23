"use strict";

const fixedNow = Number.parseInt(process.env.CI_CONSUMER_LAB_NOW_MS || "", 10);
if (Number.isSafeInteger(fixedNow) && fixedNow > 0) {
  Date.now = () => fixedNow;
}
