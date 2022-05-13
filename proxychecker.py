import argparse
import asyncio
import os
import sys
from functools import lru_cache

import aiohttp
import requests
from aiohttp_socks import ProxyConnector
from colorama import Fore, Style, init

init()


def bad(s):
    return Fore.LIGHTRED_EX + str(s) + Fore.RESET


def good(s):
    return Style.BRIGHT + Fore.GREEN + s + Fore.RESET + Style.RESET


@lru_cache()
def my_real_ip():
    return requests.get("https://ident.me").text


async def check_proxy(
    proxy_url: str, limiter: asyncio.Semaphore, timeout: int
):
    await limiter.acquire()

    async with aiohttp.ClientSession(
        connector=ProxyConnector.from_url(proxy_url), conn_timeout=timeout
    ) as session:
        try:
            async with session.get("https://httpbin.org/anything/123") as r:
                data = await r.json()
                if data["url"] != "https://httpbin.org/anything/123":
                    limiter.release()
                    print(
                        f"{bad(proxy_url):40s} BAD\t Content modified",
                        file=sys.stderr,
                    )
                    return
        except Exception:
            limiter.release()
            print(
                f"{bad(proxy_url):40s} BAD\t Failed to connect",
                file=sys.stderr,
            )
            return

        try:
            async with session.get("https://httpbin.org/headers") as r:
                data = await r.text()
                if my_real_ip() in data:
                    print(
                        f"{bad(proxy_url):40s} BAD\t REAL IP REVEALING",
                        file=sys.stderr,
                    )
                    return
        except Exception:
            print(
                f"{bad(proxy_url):40s} BAD\t Failed to connect",
                file=sys.stderr,
            )
            return
        finally:
            limiter.release()

        print(f"{good(proxy_url):32s} GOOD", file=sys.stderr)
        return proxy_url


async def main(proxy_urls, threads, timeout, good_proxies, f_good):
    limiter = asyncio.Semaphore(threads)

    tasks = [
        asyncio.ensure_future(check_proxy(url, limiter, timeout))
        for url in proxy_urls
    ]

    good = await asyncio.gather(*tasks)
    good = set(filter(None, good_proxies)) - good_proxies

    print(
        "{} good, {} bad".format(
            len(good_proxies), len(proxy_urls) - len(good_proxies)
        ),
        file=sys.stderr,
    )

    for proxy in good:
        print(proxy, file=f_good)


if __name__ == "__main__":

    def process_proxy(proxy: str) -> str:
        proxy = proxy.strip()

        if args.port:
            proxy = f"{proxy}:{args.port}"

        if args.type:
            proxy = f"{args.type}://{proxy}"

        return proxy

    p = argparse.ArgumentParser()
    p.add_argument(
        "file",
        help="Read from file (default is read from stdin)",
        type=str,
        default="-",
    )
    p.add_argument(
        "-t",
        "--threads",
        help="Number of threads (default: 10)",
        type=int,
        default=10,
    )
    p.add_argument(
        "--timeout", help="Connection timeout (default: 10)", default=10
    )
    p.add_argument(
        "-T",
        "--type",
        help="Force proxy type (socks5, http, https). "
        "No need to use this if file contains proxy URLS like socks5://...",
        type=str,
    )
    p.add_argument(
        "-O",
        "--output",
        help="Output file for good proxies (default is stdout)",
        type=str,
        default="-",
    )
    p.add_argument(
        "-p",
        "--port",
        help="Add port number",
        type=int,
    )

    args = p.parse_args()

    good_proxies = set()

    if os.path.isfile(args.output):
        with open(args.output, "tr") as f:
            good_proxies = set(map(process_proxy, f.readlines()))

    in_file = sys.stdin if args.file == "-" else open(args.file, "tr")
    out_file = sys.stdout if args.output == "-" else open(args.output, "ta")

    proxies = set(map(process_proxy, in_file.readlines()))

    if in_file is not sys.stdin:
        in_file.close()

    loop = asyncio.new_event_loop()

    try:
        loop.run_until_complete(
            main(proxies, args.threads, args.timeout, good_proxies, out_file)
        )
    except KeyboardInterrupt:
        loop.run_until_complete(loop.shutdown_asyncgens())
        for task in asyncio.all_tasks(loop):
            task.cancel()
    finally:
        loop.close()
        if out_file is not sys.stdout:
            out_file.close()
