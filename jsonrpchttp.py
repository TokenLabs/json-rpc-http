# Copyright 2014, 2015 Token Labs LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import json
import inspect
import ipaddress

# Protocol defined Rpc Server Errors

PARSE_ERROR = (-32700, "Parse error")
INVALID_REQUEST = (-32600, "Invalid Request")
METHOD_NOT_FOUND = (-32601, "Method not found")
INVALID_PARAMS = (-32602, "Invalid params")

# Extra Rpc Server Errors starting at 0

ONLY_POST_ALLOWED = (0, "Only POST allowed")
BAD_CONTENT_TYPE = (1, "Bad or missing Content-Type header")
BAD_ACCEPT = (2, "Bad Accept header")
SERVER_ERROR = (-32000, "Server error")

# HTTP Response Codes

HTTP_OK = "200 OK"
HTTP_NO_CONTENT = "204 No Content"
HTTP_BAD_REQUEST = "400 Bad Request"
HTTP_NOT_FOUND = "404 Not Found"
HTTP_METHOD_NOT_ALLOWED = "405 Method Not Allowed"
HTTP_NOT_ACCEPTABLE = "406 Not Acceptable"
HTTP_UNSUPPORTED_MEDIA = "415 Unsupported Media Type"
HTTP_SERV_ERROR = "500 Internal Server Error"
HTTP_TOO_MANY_REQUESTS = "429 Too Many Requests"

def dict_only_contains(d, keys):
    
    for k in d:
        if k not in keys:
            return False

    return True

# Server class, implementing RPC JSON 2.0 over HTTP with some restrictions

class JsonRpcServer():

    def __init__(self, methods, allowed_origins=None):

        # Ensure methods follow correct format
        
        if type(methods) is not dict:
            raise TypeError("Methods must be a dictionary")

        for key in methods:
            if type(key) is not str:
                raise TypeError("Keys in methods must be strings for the method names")

        for m in methods.values():
            if not hasattr(m, '__call__'):
                raise TypeError("Method value not callable")
            if len(inspect.getargspec(m).args) < 1:
                raise TypeError("Methods must accept the first argument for the json server argument")
        
        self.methods = methods
        self.allowed_origins = allowed_origins

    def return_result(self, result, req_id, err=False):
        return {"jsonrpc": "2.0", ("error" if err else "result"): result, "id": req_id}

    def geterrdata(self, error):
        return {
            "code": error[0],
            "message": error[1]
        }

    def return_error(self, error, req_id):
        return self.return_result(self.geterrdata(error), req_id, True)

    def not_valid_content_type(self, content_type):

        # Ignore charset
        content_type = content_type.split(";")

        if len(content_type) > 2:
            return True

        if len(content_type) > 1 and content_type[1].strip()[:8] != "charset=":
            return True

        return content_type[0] not in ['application/json-rpc', 'application/json', 'application/jsonrequest']

    def is_valid_request(self, request):
        return type(request) is dict \
        and dict_only_contains(request, ("jsonrpc", "method", "params", "id")) \
        and "jsonrpc" in request and request["jsonrpc"] == "2.0" \
        and "method" in request  and type(request["method"]) is str \
        and ("id" not in request or type(request["id"]) in [str, int]) \
        and ("params" not in request or type(request["params"]) in [list, dict])

    def process_request(self, request, ip_addr):

        # Check object corresponds to valid JSON RPC Object
        if not self.is_valid_request(request):
            return self.return_error(INVALID_REQUEST, None)

        name = request["method"]
        params = request["params"] if "params" in request else []
        req_id = request["id"] if "id" in request else None

        # Ensure we have the method

        if name not in self.methods:
            if req_id is None:
                return None
            return self.return_error(METHOD_NOT_FOUND, req_id)

        method = self.methods[name]

        # Check parameters are valid

        args = inspect.getargspec(method.original_func)
        invalid_params = False
        req_args = len(args.args) - (len(args.defaults) if args.defaults is not None else 0) - 2

        if len(params) < req_args:
            invalid_params = True

        if not invalid_params and type(params) == dict:

            for x in params:
                if x not in args.args:
                    invalid_params = True
                    break

            for x in args.args[2 : req_args + 2]:
                if x not in params:
                    invalid_params = True
                    break

        if invalid_params:
            if req_id is None:
                return None
            return self.return_error(INVALID_PARAMS, req_id)

        # Try method function. 

        res = method(self, ip_addr, **params) if type(params) is dict else method(self, ip_addr, *params)

        if req_id is None:
            return None
        if type(res) is tuple:
            return self.return_error(res, req_id)

        return self.return_result(res, req_id)

    def process_request_list(self, request, ip_addr):

        results = []

        for single_req in request:
            result = self.process_request(single_req, ip_addr)
            if result is not None:
                results.append(result)

        if len(results) == 0:
            return None

        return results

    def extra_checks(self, env, start_response, ip_addr):
        return None

    def process_call(self, env, start_response):

        # Process call without encoding into JSON.
        # Encoding should be done in the parent function as all returned values are handled in the same way
        
        headers = [('Content-Type', 'application/json-rpc')]

        if self.allowed_origins is not None:
            headers.append(('Access-Control-Allow-Origin', self.allowed_origin))

        # Only allow the POST method

        if env['REQUEST_METHOD'] != "POST":

            headers.append(("Allow", "POST"))

            if env['REQUEST_METHOD'] == "OPTIONS":
                # Preflight requests need to be implemented for Ajax queries
                status = HTTP_OK
                headers.append(('Access-Control-Allow-Headers', 'Content-Type, Accept, Content-Length, Host, Origin, User-Agent, Referer'))
                start_response(HTTP_OK, headers)
                return None
            else:
                start_response(HTTP_METHOD_NOT_ALLOWED, headers)
                return self.return_error(ONLY_POST_ALLOWED, None)

        # Check headers

        if 'CONTENT_TYPE' not in env or self.not_valid_content_type(env['CONTENT_TYPE']):
            start_response(HTTP_UNSUPPORTED_MEDIA, headers)
            return self.return_error(BAD_CONTENT_TYPE, None)

        if 'HTTP_ACCEPT' in env and self.not_valid_content_type(env['HTTP_ACCEPT']):
            start_response(HTTP_NOT_ACCEPTABLE, headers)
            return self.return_error(BAD_ACCEPT, None)

        if 'REMOTE_ADDR' in env:
            # Get ip address information
            ip_addr = ipaddress.ip_address(env['REMOTE_ADDR']).packed
        else:
            ip_addr = None

        # Check extra_checks that may be overriden

        resp = self.extra_checks(env, start_response, ip_addr)
        if resp is not None:
            return resp

        # Read POST data

        try:
            request = json.loads(env['wsgi.input'].read().decode('utf-8'))
        except ValueError:
            start_response(HTTP_BAD_REQUEST, headers)
            return self.return_error(PARSE_ERROR, None)

        # Process array of objects or single object

        if type(request) is list:
            result = self.process_request_list(request, ip_addr)
        else:
            result = self.process_request(request, ip_addr)

        if result is None:
            start_response(HTTP_NO_CONTENT, [])
        else:
            start_response(HTTP_OK, headers)

        return result

    def __call__(self, env, start_response):
        result = self.process_call(env, start_response)
        return [b"" if result is None else json.dumps(result).encode()]

