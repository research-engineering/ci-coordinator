'use strict';

const EXPECTED_NODE_VERSION = 'v24.21.0';

function assertExactNodeVersion(version) {
  if (version !== EXPECTED_NODE_VERSION) {
    throw new Error('Node.js runtime version is not admitted');
  }
}

assertExactNodeVersion(process.version);
globalThis.__CI_CONSUMER_LAB_NODE_RUNTIME_ADMITTED__ = EXPECTED_NODE_VERSION;

module.exports = Object.freeze({ assertExactNodeVersion });
