/**
 * This file contains the Node.js code, used for the optional status check feature
 * It accepts a single url parameter, and will make an empty GET request to that
 * endpoint, and then resolve the response status code, time taken, and short message
 */
const axios = require('axios').default;
const https = require('https');

/* Determines if successful from the HTTP response code */
const getResponseType = (code) => {
  if (Number.isNaN(code)) return false;
  const numericCode = parseInt(code, 10);
  return (numericCode >= 200 && numericCode <= 302);
};

/* Makes human-readable response text for successful check */
const makeMessageText = (data) => `${data.successStatus ? '✅' : '⚠️'} `
  + `${data.serverName || 'Server'} responded with `
  + `${data.statusCode} - ${data.statusText}. `
  + `\n⏱️Took ${data.timeTaken} ms`;

/* Makes human-readable response text for failed check */
const makeErrorMessage = (data) => `❌ Service Unavailable: ${data.hostname || 'Server'} `
  + `resulted in ${data.code || 'a fatal error'} ${data.errno ? `(${data.errno})` : ''}`;

const makeErrorMessage2 = (data) => '❌ Service Error - '
  + `${data.status} - ${data.statusText}`;

/* Kicks of a HTTP request, then formats and renders results */
const makeRequest = (url, headers, insecure, render) => {
  const startTime = new Date();
  const requestMaker = axios.create({
    httpsAgent: new https.Agent({
      rejectUnauthorized: !insecure,
    }),
  });
  requestMaker.get(url, { headers })
    .then((response) => {
      const statusCode = response.status;
      const { statusText } = response;
      const successStatus = getResponseType(statusCode);
      const serverName = response.request.socket.servername;
      const timeTaken = (new Date() - startTime);
      const results = {
        statusCode, statusText, serverName, successStatus, timeTaken,
      };
      const messageText = makeMessageText(results);
      results.message = messageText;
      return results;
    })
    .catch((error) => {
      render(JSON.stringify({
        successStatus: false,
        message: error.response ? makeErrorMessage2(error.response) : makeErrorMessage(error),
      }));
    }).then((results) => {
      render(JSON.stringify(results));
    });
};

const decodeHeaders = (maybeHeaders) => {
  if (!maybeHeaders) return {};
  const decodedHeaders = decodeURIComponent(maybeHeaders);
  let parsedHeaders = {};
  try {
    parsedHeaders = JSON.parse(decodedHeaders);
  } catch (e) { /* Not valid JSON, will just return false */ }
  return parsedHeaders;
};

/* Main function, will check if a URL present, and call function */
module.exports = (paramStr, render) => {
  if (!paramStr || !paramStr.includes('=')) {
    render(JSON.stringify({
      success: false,
      message: '❌ Malformed URL',
    }));
  } else {
    // Prepare the parameters, which are got from the URL
    const params = new URLSearchParams(paramStr);
    const url = decodeURIComponent(params.get('url'));
    const headers = decodeHeaders(params.get('headers'));
    const enableInsecure = !!params.get('enableInsecure');
    makeRequest(url, headers, enableInsecure, render);
  }
};
