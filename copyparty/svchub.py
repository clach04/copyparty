# coding: utf-8
from __future__ import print_function, unicode_literals

import argparse
import base64
import calendar
import errno
import gzip
import logging
import os
import re
import shlex
import signal
import socket
import string
import sys
import threading
import time
from datetime import datetime, timedelta

# from inspect import currentframe
# print(currentframe().f_lineno)


if True:  # pylint: disable=using-constant-test
    from types import FrameType

    import typing
    from typing import Any, Optional, Union

from .__init__ import ANYWIN, EXE, MACOS, TYPE_CHECKING, EnvParams, unicode
from .authsrv import AuthSrv
from .mtag import HAVE_FFMPEG, HAVE_FFPROBE
from .tcpsrv import TcpSrv
from .th_srv import HAVE_PIL, HAVE_VIPS, HAVE_WEBP, ThumbSrv
from .up2k import Up2k
from .util import (
    FFMPEG_URL,
    VERSIONS,
    Daemon,
    Garda,
    HLog,
    HMaccas,
    alltrace,
    ansi_re,
    min_ex,
    mp,
    pybin,
    start_log_thrs,
    start_stackmon,
)

if TYPE_CHECKING:
    try:
        from .mdns import MDNS
        from .ssdp import SSDPd
    except:
        pass


class SvcHub(object):
    """
    Hosts all services which cannot be parallelized due to reliance on monolithic resources.
    Creates a Broker which does most of the heavy stuff; hosted services can use this to perform work:
        hub.broker.<say|ask>(destination, args_list).

    Either BrokerThr (plain threads) or BrokerMP (multiprocessing) is used depending on configuration.
    Nothing is returned synchronously; if you want any value returned from the call,
    put() can return a queue (if want_reply=True) which has a blocking get() with the response.
    """

    def __init__(
        self,
        args: argparse.Namespace,
        dargs: argparse.Namespace,
        argv: list[str],
        printed: str,
    ) -> None:
        self.args = args
        self.dargs = dargs
        self.argv = argv
        self.E: EnvParams = args.E
        self.no_ansi = args.no_ansi
        self.logf: Optional[typing.TextIO] = None
        self.logf_base_fn = ""
        self.stop_req = False
        self.stopping = False
        self.stopped = False
        self.reload_req = False
        self.reloading = False
        self.stop_cond = threading.Condition()
        self.nsigs = 3
        self.retcode = 0
        self.httpsrv_up = 0

        self.log_mutex = threading.Lock()
        self.next_day = 0
        self.tstack = 0.0

        self.iphash = HMaccas(os.path.join(self.E.cfg, "iphash"), 8)

        # for non-http clients (ftp)
        self.bans: dict[str, int] = {}
        self.gpwd = Garda(self.args.ban_pw)
        self.g404 = Garda(self.args.ban_404)

        if args.sss or args.s >= 3:
            args.ss = True
            args.no_dav = True
            args.no_logues = True
            args.no_readme = True
            args.lo = args.lo or "cpp-%Y-%m%d-%H%M%S.txt.xz"
            args.ls = args.ls or "**,*,ln,p,r"

        if args.ss or args.s >= 2:
            args.s = True
            args.unpost = 0
            args.no_del = True
            args.no_mv = True
            args.hardlink = True
            args.vague_403 = True
            args.ban_404 = "50,60,1440"
            args.nih = True

        if args.s:
            args.dotpart = True
            args.no_thumb = True
            args.no_mtag_ff = True
            args.no_robots = True
            args.force_js = True

        if not self._process_config():
            raise Exception("bad config")

        self.log = self._log_disabled if args.q else self._log_enabled
        if args.lo:
            self._setup_logfile(printed)

        lg = logging.getLogger()
        lh = HLog(self.log)
        lg.handlers = [lh]
        lg.setLevel(logging.DEBUG)

        if args.stackmon:
            start_stackmon(args.stackmon, 0)

        if args.log_thrs:
            start_log_thrs(self.log, args.log_thrs, 0)

        if not args.use_fpool and args.j != 1:
            args.no_fpool = True
            t = "multithreading enabled with -j {}, so disabling fpool -- this can reduce upload performance on some filesystems"
            self.log("root", t.format(args.j))

        if not args.no_fpool and args.j != 1:
            t = "WARNING: ignoring --use-fpool because multithreading (-j{}) is enabled"
            self.log("root", t.format(args.j), c=3)
            args.no_fpool = True

        bri = "zy"[args.theme % 2 :][:1]
        ch = "abcdefghijklmnopqrstuvwx"[int(args.theme / 2)]
        args.theme = "{0}{1} {0} {1}".format(ch, bri)

        if args.log_fk:
            args.log_fk = re.compile(args.log_fk)

        # initiate all services to manage
        self.asrv = AuthSrv(self.args, self.log, dargs=self.dargs)

        if args.cgen:
            self.asrv.cgen()

        if args.exit == "cfg":
            sys.exit(0)

        if args.ls:
            self.asrv.dbg_ls()

        if not ANYWIN:
            self._setlimits()

        self.log("root", "max clients: {}".format(self.args.nc))

        self.tcpsrv = TcpSrv(self)
        self.up2k = Up2k(self)

        decs = {k: 1 for k in self.args.th_dec.split(",")}
        if not HAVE_VIPS:
            decs.pop("vips", None)
        if not HAVE_PIL:
            decs.pop("pil", None)
        if not HAVE_FFMPEG or not HAVE_FFPROBE:
            decs.pop("ff", None)

        self.args.th_dec = list(decs.keys())
        self.thumbsrv = None
        want_ff = False
        if not args.no_thumb:
            t = ", ".join(self.args.th_dec) or "(None available)"
            self.log("thumb", "decoder preference: {}".format(t))

            if "pil" in self.args.th_dec and not HAVE_WEBP:
                msg = "disabling webp thumbnails because either libwebp is not available or your Pillow is too old"
                self.log("thumb", msg, c=3)

            if self.args.th_dec:
                self.thumbsrv = ThumbSrv(self)
            else:
                want_ff = True
                msg = "need either Pillow, pyvips, or FFmpeg to create thumbnails; for example:\n{0}{1} -m pip install --user Pillow\n{0}{1} -m pip install --user pyvips\n{0}apt install ffmpeg"
                msg = msg.format(" " * 37, os.path.basename(pybin))
                if EXE:
                    msg = "copyparty.exe cannot use Pillow or pyvips; need ffprobe.exe and ffmpeg.exe to create thumbnails"

                self.log("thumb", msg, c=3)

        if not args.no_acode and args.no_thumb:
            msg = "setting --no-acode because --no-thumb (sorry)"
            self.log("thumb", msg, c=6)
            args.no_acode = True

        if not args.no_acode and (not HAVE_FFMPEG or not HAVE_FFPROBE):
            msg = "setting --no-acode because either FFmpeg or FFprobe is not available"
            self.log("thumb", msg, c=6)
            args.no_acode = True
            want_ff = True

        if want_ff and ANYWIN:
            self.log("thumb", "download FFmpeg to fix it:\033[0m " + FFMPEG_URL, 3)

        args.th_poke = min(args.th_poke, args.th_maxage, args.ac_maxage)

        zms = ""
        if not args.https_only:
            zms += "d"
        if not args.http_only:
            zms += "D"

        if args.ftp or args.ftps:
            from .ftpd import Ftpd

            self.ftpd = Ftpd(self)
            zms += "f" if args.ftp else "F"

        if args.smb:
            # impacket.dcerpc is noisy about listen timeouts
            sto = socket.getdefaulttimeout()
            socket.setdefaulttimeout(None)

            from .smbd import SMB

            self.smbd = SMB(self)
            socket.setdefaulttimeout(sto)
            self.smbd.start()
            zms += "s"

        if not args.zms:
            args.zms = zms

        self.zc_ngen = 0
        self.mdns: Optional["MDNS"] = None
        self.ssdp: Optional["SSDPd"] = None

        # decide which worker impl to use
        if self.check_mp_enable():
            from .broker_mp import BrokerMp as Broker
        else:
            from .broker_thr import BrokerThr as Broker  # type: ignore

        self.broker = Broker(self)

    def thr_httpsrv_up(self) -> None:
        time.sleep(1 if self.args.ign_ebind_all else 5)
        expected = self.broker.num_workers * self.tcpsrv.nsrv
        failed = expected - self.httpsrv_up
        if not failed:
            return

        if self.args.ign_ebind_all:
            if not self.tcpsrv.srv:
                for _ in range(self.broker.num_workers):
                    self.broker.say("cb_httpsrv_up")
            return

        if self.args.ign_ebind and self.tcpsrv.srv:
            return

        t = "{}/{} workers failed to start"
        t = t.format(failed, expected)
        self.log("root", t, 1)

        self.retcode = 1
        self.sigterm()

    def sigterm(self) -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    def cb_httpsrv_up(self) -> None:
        self.httpsrv_up += 1
        if self.httpsrv_up != self.broker.num_workers:
            return

        time.sleep(0.1)  # purely cosmetic dw
        if self.tcpsrv.qr:
            self.log("qr-code", self.tcpsrv.qr)
        else:
            self.log("root", "workers OK\n")

        self.up2k.init_vols()

        Daemon(self.sd_notify, "sd-notify")

    def _process_config(self) -> bool:
        al = self.args

        al.zm_on = al.zm_on or al.z_on
        al.zs_on = al.zs_on or al.z_on
        al.zm_off = al.zm_off or al.z_off
        al.zs_off = al.zs_off or al.z_off
        ns = "zm_on zm_off zs_on zs_off acao acam"
        for n in ns.split(" "):
            vs = getattr(al, n).split(",")
            vs = [x.strip() for x in vs]
            vs = [x for x in vs if x]
            setattr(al, n, vs)

        ns = "acao acam"
        for n in ns.split(" "):
            vs = getattr(al, n)
            vd = {zs: 1 for zs in vs}
            setattr(al, n, vd)

        ns = "acao"
        for n in ns.split(" "):
            vs = getattr(al, n)
            vs = [x.lower() for x in vs]
            setattr(al, n, vs)

        R = al.rp_loc
        if "//" in R or ":" in R:
            t = "found URL in --rp-loc; it should be just the location, for example /foo/bar"
            raise Exception(t)

        al.R = R = R.strip("/")
        al.SR = "/" + R if R else ""
        al.RS = R + "/" if R else ""
        al.SRS = "/" + R + "/" if R else "/"

        if al.rsp_jtr:
            al.rsp_slp = 0.000001

        al.th_covers = set(al.th_covers.split(","))

        for k in "c".split(" "):
            vl = getattr(al, k)
            if not vl:
                continue

            vl = [os.path.expanduser(x) if x.startswith("~") else x for x in vl]
            setattr(al, k, vl)

        for k in "lo hist ssl_log".split(" "):
            vs = getattr(al, k)
            if vs and vs.startswith("~"):
                setattr(al, k, os.path.expanduser(vs))

        return True

    def _setlimits(self) -> None:
        try:
            import resource

            soft, hard = [
                x if x > 0 else 1024 * 1024
                for x in list(resource.getrlimit(resource.RLIMIT_NOFILE))
            ]
        except:
            self.log("root", "failed to read rlimits from os", 6)
            return

        if not soft or not hard:
            t = "got bogus rlimits from os ({}, {})"
            self.log("root", t.format(soft, hard), 6)
            return

        want = self.args.nc * 4
        new_soft = min(hard, want)
        if new_soft < soft:
            return

        # t = "requesting rlimit_nofile({}), have {}"
        # self.log("root", t.format(new_soft, soft), 6)

        try:
            import resource

            resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
            soft = new_soft
        except:
            t = "rlimit denied; max open files: {}"
            self.log("root", t.format(soft), 3)
            return

        if soft < want:
            t = "max open files: {} (wanted {} for -nc {})"
            self.log("root", t.format(soft, want, self.args.nc), 3)
            self.args.nc = min(self.args.nc, soft // 2)

    def _logname(self) -> str:
        dt = datetime.utcnow()
        fn = str(self.args.lo)
        for fs in "YmdHMS":
            fs = "%" + fs
            if fs in fn:
                fn = fn.replace(fs, dt.strftime(fs))

        return fn

    def _setup_logfile(self, printed: str) -> None:
        base_fn = fn = sel_fn = self._logname()
        do_xz = fn.lower().endswith(".xz")
        if fn != self.args.lo:
            ctr = 0
            # yup this is a race; if started sufficiently concurrently, two
            # copyparties can grab the same logfile (considered and ignored)
            while os.path.exists(sel_fn):
                ctr += 1
                sel_fn = "{}.{}".format(fn, ctr)

        fn = sel_fn

        try:
            if do_xz:
                import lzma

                lh = lzma.open(fn, "wt", encoding="utf-8", errors="replace", preset=0)
            else:
                lh = open(fn, "wt", encoding="utf-8", errors="replace")
        except:
            import codecs

            lh = codecs.open(fn, "w", encoding="utf-8", errors="replace")

        argv = [pybin] + self.argv
        if hasattr(shlex, "quote"):
            argv = [shlex.quote(x) for x in argv]
        else:
            argv = ['"{}"'.format(x) for x in argv]

        msg = "[+] opened logfile [{}]\n".format(fn)
        printed += msg
        t = "t0: {:.3f}\nargv: {}\n\n{}"
        lh.write(t.format(self.E.t0, " ".join(argv), printed))
        self.logf = lh
        self.logf_base_fn = base_fn
        print(msg, end="")

    def run(self) -> None:
        self.tcpsrv.run()
        if getattr(self.args, "z_chk", 0) and (
            getattr(self.args, "zm", False) or getattr(self.args, "zs", False)
        ):
            Daemon(self.tcpsrv.netmon, "netmon")

        Daemon(self.thr_httpsrv_up, "sig-hsrv-up2")

        sigs = [signal.SIGINT, signal.SIGTERM]
        if not ANYWIN:
            sigs.append(signal.SIGUSR1)

        for sig in sigs:
            signal.signal(sig, self.signal_handler)

        # macos hangs after shutdown on sigterm with while-sleep,
        # windows cannot ^c stop_cond (and win10 does the macos thing but winxp is fine??)
        # linux is fine with both,
        # never lucky
        if ANYWIN:
            # msys-python probably fine but >msys-python
            Daemon(self.stop_thr, "svchub-sig")

            try:
                while not self.stop_req:
                    time.sleep(1)
            except:
                pass

            self.shutdown()
            # cant join; eats signals on win10
            while not self.stopped:
                time.sleep(0.1)
        else:
            self.stop_thr()

    def start_zeroconf(self) -> None:
        self.zc_ngen += 1

        if getattr(self.args, "zm", False):
            try:
                from .mdns import MDNS

                if self.mdns:
                    self.mdns.stop(True)

                self.mdns = MDNS(self, self.zc_ngen)
                Daemon(self.mdns.run, "mdns")
            except:
                self.log("root", "mdns startup failed;\n" + min_ex(), 3)

        if getattr(self.args, "zs", False):
            try:
                from .ssdp import SSDPd

                if self.ssdp:
                    self.ssdp.stop()

                self.ssdp = SSDPd(self, self.zc_ngen)
                Daemon(self.ssdp.run, "ssdp")
            except:
                self.log("root", "ssdp startup failed;\n" + min_ex(), 3)

    def reload(self) -> str:
        if self.reloading:
            return "cannot reload; already in progress"

        self.reloading = True
        Daemon(self._reload, "reloading")
        return "reload initiated"

    def _reload(self) -> None:
        self.log("root", "reload scheduled")
        with self.up2k.mutex:
            self.asrv.reload()
            self.up2k.reload()
            self.broker.reload()

        self.reloading = False

    def stop_thr(self) -> None:
        while not self.stop_req:
            with self.stop_cond:
                self.stop_cond.wait(9001)

            if self.reload_req:
                self.reload_req = False
                self.reload()

        self.shutdown()

    def kill9(self, delay: float = 0.0) -> None:
        if delay > 0.01:
            time.sleep(delay)
            print("component stuck; issuing sigkill")
            time.sleep(0.1)

        if ANYWIN:
            os.system("taskkill /f /pid {}".format(os.getpid()))
        else:
            os.kill(os.getpid(), signal.SIGKILL)

    def signal_handler(self, sig: int, frame: Optional[FrameType]) -> None:
        if self.stopping:
            if self.nsigs <= 0:
                try:
                    threading.Thread(target=self.pr, args=("OMBO BREAKER",)).start()
                    time.sleep(0.1)
                except:
                    pass

                self.kill9()
            else:
                self.nsigs -= 1
                return

        if not ANYWIN and sig == signal.SIGUSR1:
            self.reload_req = True
        else:
            self.stop_req = True

        with self.stop_cond:
            self.stop_cond.notify_all()

    def shutdown(self) -> None:
        if self.stopping:
            return

        # start_log_thrs(print, 0.1, 1)

        self.stopping = True
        self.stop_req = True
        with self.stop_cond:
            self.stop_cond.notify_all()

        ret = 1
        try:
            self.pr("OPYTHAT")
            slp = 0.0

            if self.mdns:
                Daemon(self.mdns.stop)
                slp = time.time() + 0.5

            if self.ssdp:
                Daemon(self.ssdp.stop)
                slp = time.time() + 0.5

            self.broker.shutdown()
            self.tcpsrv.shutdown()
            self.up2k.shutdown()
            if self.thumbsrv:
                self.thumbsrv.shutdown()

                for n in range(200):  # 10s
                    time.sleep(0.05)
                    if self.thumbsrv.stopped():
                        break

                    if n == 3:
                        self.pr("waiting for thumbsrv (10sec)...")

            if hasattr(self, "smbd"):
                slp = max(slp, time.time() + 0.5)
                Daemon(self.kill9, a=(1,))
                Daemon(self.smbd.stop)

            while time.time() < slp:
                time.sleep(0.1)

            self.pr("nailed it", end="")
            ret = self.retcode
        except:
            self.pr("\033[31m[ error during shutdown ]\n{}\033[0m".format(min_ex()))
            raise
        finally:
            if self.args.wintitle:
                print("\033]0;\033\\", file=sys.stderr, end="")
                sys.stderr.flush()

            self.pr("\033[0m")
            if self.logf:
                self.logf.close()

            self.stopped = True
            sys.exit(ret)

    def _log_disabled(self, src: str, msg: str, c: Union[int, str] = 0) -> None:
        if not self.logf:
            return

        with self.log_mutex:
            zd = datetime.utcnow()
            ts = "%04d-%04d-%06d.%03d" % (
                zd.year,
                zd.month * 100 + zd.day,
                (zd.hour * 100 + zd.minute) * 100 + zd.second,
                zd.microsecond // 1000,
            )
            self.logf.write("@%s [%s\033[0m] %s\n" % (ts, src, msg))

            now = time.time()
            if now >= self.next_day:
                self._set_next_day()

    def _set_next_day(self) -> None:
        if self.next_day and self.logf and self.logf_base_fn != self._logname():
            self.logf.close()
            self._setup_logfile("")

        dt = datetime.utcnow()

        # unix timestamp of next 00:00:00 (leap-seconds safe)
        day_now = dt.day
        while dt.day == day_now:
            dt += timedelta(hours=12)

        dt = dt.replace(hour=0, minute=0, second=0)
        self.next_day = calendar.timegm(dt.utctimetuple())

    def _log_enabled(self, src: str, msg: str, c: Union[int, str] = 0) -> None:
        """handles logging from all components"""
        with self.log_mutex:
            now = time.time()
            if now >= self.next_day:
                dt = datetime.utcfromtimestamp(now)
                zs = "{}\n" if self.no_ansi else "\033[36m{}\033[0m\n"
                zs = zs.format(dt.strftime("%Y-%m-%d"))
                print(zs, end="")
                self._set_next_day()
                if self.logf:
                    self.logf.write(zs)

            fmt = "\033[36m%s \033[33m%-21s \033[0m%s\n"
            if self.no_ansi:
                fmt = "%s %-21s %s\n"
                if "\033" in msg:
                    msg = ansi_re.sub("", msg)
                if "\033" in src:
                    src = ansi_re.sub("", src)
            elif c:
                if isinstance(c, int):
                    msg = "\033[3%sm%s\033[0m" % (c, msg)
                elif "\033" not in c:
                    msg = "\033[%sm%s\033[0m" % (c, msg)
                else:
                    msg = "%s%s\033[0m" % (c, msg)

            zd = datetime.utcfromtimestamp(now)
            ts = "%02d:%02d:%02d.%03d" % (
                zd.hour,
                zd.minute,
                zd.second,
                zd.microsecond // 1000,
            )
            msg = fmt % (ts, src, msg)
            try:
                print(msg, end="")
            except UnicodeEncodeError:
                try:
                    print(msg.encode("utf-8", "replace").decode(), end="")
                except:
                    print(msg.encode("ascii", "replace").decode(), end="")
            except OSError as ex:
                if ex.errno != errno.EPIPE:
                    raise

            if self.logf:
                self.logf.write(msg)

    def pr(self, *a: Any, **ka: Any) -> None:
        try:
            with self.log_mutex:
                print(*a, **ka)
        except OSError as ex:
            if ex.errno != errno.EPIPE:
                raise

    def check_mp_support(self) -> str:
        if MACOS:
            return "multiprocessing is wonky on mac osx;"
        elif sys.version_info < (3, 3):
            return "need python 3.3 or newer for multiprocessing;"

        try:
            x: mp.Queue[tuple[str, str]] = mp.Queue(1)
            x.put(("foo", "bar"))
            if x.get()[0] != "foo":
                raise Exception()
        except:
            return "multiprocessing is not supported on your platform;"

        return ""

    def check_mp_enable(self) -> bool:
        if self.args.j == 1:
            return False

        try:
            if mp.cpu_count() <= 1:
                raise Exception()
        except:
            self.log("svchub", "only one CPU detected; multiprocessing disabled")
            return False

        try:
            # support vscode debugger (bonus: same behavior as on windows)
            mp.set_start_method("spawn", True)
        except AttributeError:
            # py2.7 probably, anyways dontcare
            pass

        err = self.check_mp_support()
        if not err:
            return True
        else:
            self.log("svchub", err)
            self.log("svchub", "cannot efficiently use multiple CPU cores")
            return False

    def sd_notify(self) -> None:
        try:
            zb = os.getenv("NOTIFY_SOCKET")
            if not zb:
                return

            addr = unicode(zb)
            if addr.startswith("@"):
                addr = "\0" + addr[1:]

            t = "".join(x for x in addr if x in string.printable)
            self.log("sd_notify", t)

            sck = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sck.connect(addr)
            sck.sendall(b"READY=1")
        except:
            self.log("sd_notify", min_ex())

    def log_stacks(self) -> None:
        td = time.time() - self.tstack
        if td < 300:
            self.log("stacks", "cooldown {}".format(td))
            return

        self.tstack = time.time()
        zs = "{}\n{}".format(VERSIONS, alltrace())
        zb = zs.encode("utf-8", "replace")
        zb = gzip.compress(zb)
        zs = base64.b64encode(zb).decode("ascii")
        self.log("stacks", zs)
