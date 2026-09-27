#define _POSIX_C_SOURCE 200809L

#include "server_native.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET zy2_socket;
#define ZY2_INVALID_SOCKET INVALID_SOCKET
#define zy2_close_socket closesocket
#else
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
typedef int zy2_socket;
#define ZY2_INVALID_SOCKET (-1)
#define zy2_close_socket close
#endif

static _Thread_local char zy2_server_message[512] = "";

static void zy2_server_set_error(const char* action) {
#ifdef _WIN32
    snprintf(zy2_server_message, sizeof(zy2_server_message), "%s failed (socket error %d)", action, WSAGetLastError());
#else
    snprintf(zy2_server_message, sizeof(zy2_server_message), "%s failed: %s", action, strerror(errno));
#endif
}

static int zy2_send_all(zy2_socket socket_value, const char* data, size_t length) {
    while (length > 0) {
#ifdef _WIN32
        int sent = send(socket_value, data, length > INT_MAX ? INT_MAX : (int)length, 0);
#else
        ssize_t sent = send(socket_value, data, length, 0);
#endif
        if (sent <= 0) return -1;
        data += sent;
        length -= (size_t)sent;
    }
    return 0;
}

typedef ZL_String (*zy3_server_handler_call)(void* env, ZL_String method, ZL_String path, ZL_String body);

static int32_t zy3_server_serve_internal(
    ZL_String host_value,
    int32_t port,
    ZL_String body_value,
    int32_t max_requests,
    ZL_Function handler,
    int use_handler
) {
    const char* host = zl_string_data(host_value);
    const char* fixed_body = zl_string_data(body_value);
    struct addrinfo hints;
    struct addrinfo* addresses = NULL;
    struct addrinfo* address;
    zy2_socket listener = ZY2_INVALID_SOCKET;
    char port_text[16];
    int32_t handled = 0;
#ifdef _WIN32
    WSADATA winsock;
#endif

    zy2_server_message[0] = '\0';
    if (!host || (!use_handler && !fixed_body) || port < 1 || port > 65535 || max_requests < 1) {
        snprintf(zy2_server_message, sizeof(zy2_server_message), "invalid host, port, body, or request limit");
        return -1;
    }
    if (use_handler && !zl_fn_matches(handler, "fn(str, str, str) str")) {
        snprintf(zy2_server_message, sizeof(zy2_server_message), "HTTP handler must have type fn(str, str, str) str");
        return -1;
    }
#ifdef _WIN32
    if (WSAStartup(MAKEWORD(2, 2), &winsock) != 0) {
        zy2_server_set_error("WSAStartup");
        return -1;
    }
#endif
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_flags = AI_PASSIVE;
    snprintf(port_text, sizeof(port_text), "%d", (int)port);
    if (getaddrinfo(host[0] ? host : NULL, port_text, &hints, &addresses) != 0) {
        snprintf(zy2_server_message, sizeof(zy2_server_message), "cannot resolve bind address");
        goto fail;
    }
    for (address = addresses; address; address = address->ai_next) {
        int reuse = 1;
        listener = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
        if (listener == ZY2_INVALID_SOCKET) continue;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, (const char*)&reuse, sizeof(reuse));
        if (bind(listener, address->ai_addr, (int)address->ai_addrlen) == 0) break;
        zy2_close_socket(listener);
        listener = ZY2_INVALID_SOCKET;
    }
    if (listener == ZY2_INVALID_SOCKET) {
        zy2_server_set_error("bind");
        goto fail;
    }
    if (listen(listener, 16) != 0) {
        zy2_server_set_error("listen");
        goto fail;
    }

    while (handled < max_requests) {
        char request[65536];
        char header[512];
        zy2_socket client = accept(listener, NULL, NULL);
        int header_length;
        int received;
        const char* body = fixed_body;
        ZL_String generated = {0};
        if (client == ZY2_INVALID_SOCKET) {
            zy2_server_set_error("accept");
            goto fail;
        }
#ifdef _WIN32
        received = recv(client, request, (int)sizeof(request) - 1, 0);
#else
        received = (int)recv(client, request, sizeof(request) - 1u, 0);
#endif
        if (received <= 0) {
            zy2_close_socket(client);
            zy2_server_set_error("receive");
            goto fail;
        }
        request[received] = '\0';
        if (use_handler) {
            char method[32] = "";
            char request_path[2048] = "";
            const char* payload = strstr(request, "\r\n\r\n");
            if (sscanf(request, "%31s %2047s", method, request_path) != 2) {
                zy2_close_socket(client);
                snprintf(zy2_server_message, sizeof(zy2_server_message), "cannot parse HTTP request line");
                goto fail;
            }
            payload = payload ? payload + 4 : "";
            generated = ZL_FN_CALL_AS(zy3_server_handler_call, handler)(
                handler.env,
                zl_string_borrow(method),
                zl_string_borrow(request_path),
                zl_string_borrow(payload)
            );
            body = zl_string_data(generated);
            if (!body) body = "";
        }
        header_length = snprintf(
            header,
            sizeof(header),
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n",
            strlen(body)
        );
        if (header_length < 0 || (size_t)header_length >= sizeof(header)
                || zy2_send_all(client, header, (size_t)header_length) != 0
                || zy2_send_all(client, body, strlen(body)) != 0) {
            zl_string_release(generated);
            zy2_close_socket(client);
            zy2_server_set_error("send");
            goto fail;
        }
        zl_string_release(generated);
        zy2_close_socket(client);
        handled += 1;
    }

    zy2_close_socket(listener);
    freeaddrinfo(addresses);
#ifdef _WIN32
    WSACleanup();
#endif
    return handled;

fail:
    if (listener != ZY2_INVALID_SOCKET) zy2_close_socket(listener);
    if (addresses) freeaddrinfo(addresses);
#ifdef _WIN32
    WSACleanup();
#endif
    return -1;
}

int32_t zy2_server_serve(ZL_String host, int32_t port, ZL_String body, int32_t max_requests) {
    ZL_Function empty = {0};
    return zy3_server_serve_internal(host, port, body, max_requests, empty, 0);
}

int32_t zy3_server_serve_handler(ZL_String host, int32_t port, int32_t max_requests, ZL_Function handler) {
    return zy3_server_serve_internal(host, port, zl_string_borrow(""), max_requests, handler, 1);
}

ZL_String zy2_server_error(void) {
    return zl_string_copy(zy2_server_message);
}
