"use strict";

const { main: consumePlan } = require("./consume_plan.cjs");
const { main: validateGate } = require("./validate_gate.cjs");
const { main: validatePlan } = require("./validate_plan.cjs");

const COMMANDS = Object.freeze({
  "consume-plan": consumePlan,
  "validate-gate": validateGate,
  "validate-plan": validatePlan,
});

function main() {
  const arguments_ = process.argv.slice(2);
  if (arguments_.length !== 1) {
    process.exitCode = 1;
    return;
  }
  const command = Object.hasOwn(COMMANDS, arguments_[0])
    ? COMMANDS[arguments_[0]]
    : undefined;
  if (command === undefined) {
    process.exitCode = 1;
    return;
  }
  command();
}

main();
