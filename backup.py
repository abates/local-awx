#!/usr/bin/env python3

import argparse
import base64
from datetime import datetime
import json
import os
import shutil
import subprocess
import sys
import tarfile

class Deployment:
    def __init__(self, namespace):
        self.namespace = namespace
        self.operator_name = self.run_k8s("get", "awx", "-o", "jsonpath={.items[0].metadata.name}")
        self.db_pod = self.run_k8s("get", "pod", "--selector=app.kubernetes.io/component=database", "-o", "jsonpath={.items[0].metadata.name}")
        self.db_configuration = json.loads(self.run_k8s("get", "secret", f"{self.operator_name}-postgres-configuration", "-o", "jsonpath={.data}"))
        self.decode(self.db_configuration, *self.db_configuration.keys())

    def run_k8s(self, *cmd, stdin=None, stdout=None, stderr=None, decode=True):
        command = [
            "kubectl",
            "-n",
            self.namespace,
            *cmd
        ]
        result = subprocess.run(command, capture_output=(stdout is None), input=stdin, stdout=stdout, stderr=stderr)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)
        if stdout is None:
            if decode:
                return result.stdout.decode()
            return result.stdout

    def exec_k8s(self, pod, container, *cmd, stdin=None, stdout=None, stderr=None, decode=True):
        exec_cmd = ["exec"]
        if stdin:
            exec_cmd.append("-i")
        exec_cmd.extend([
            pod,
            "-c", container,
            "--",
            *cmd,
        ])
        return self.run_k8s(*exec_cmd, stdin=stdin, stdout=stdout, stderr=stderr, decode=decode)

    def exec_db(self, cmd, *args, stdin=None, stdout=None, stderr=None, decode=True):
        return self.exec_k8s(
            self.db_pod,
            "postgres",
            "env", f"PGPASSWORD={self.db_configuration['password']}",
            cmd,
            "-U", self.db_configuration["username"],
            "-h", "localhost",
            *args,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            decode=decode,
        )

    @staticmethod
    def decode(data, *keys):
        for key in keys:
            data[key] = base64.b64decode(data[key]).decode("utf-8")

    def backup(self, name):
        os.mkdir(name)
        with open(os.path.join(name, "backup.dump"), "wb") as dumpfile:
            self.exec_db(
                "pg_dump",
                "-Fc",
                self.db_configuration["database"],
                decode=False,
                stdout=dumpfile,
            )

        output = self.run_k8s("get", "secrets", "-o", "jsonpath={.items[*].metadata.name}")
        for secret_name in output.split():
            if secret_name.endswith("postgres-configuration"):
                continue

            with open(os.path.join(name, f"{secret_name}_secret.json"), "w") as secret_file:
                secret = self.run_k8s("get", "secret", secret_name, "-o", "json", stdout=secret_file)

        with tarfile.open(f"{name}.tar", "w") as tar:
            tar.add(name)

        shutil.rmtree(name)

    def recreate_db(self, backup):
        # drop the existing database
        self.exec_db(
            "dropdb",
            "--force",  # force will disconnect existing clients
            "--if-exists",
            self.db_configuration['database'],
        )

        # recreate the empty database
        self.exec_db(
            "createdb",
            f"--owner={self.db_configuration['username']}",
            self.db_configuration['database'],
        )

        # load the backup file
        self.exec_db(
            "pg_restore",
            "-d", self.db_configuration['database'],
            "-x", "-1",
            "--verbose",
            stdin=backup,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    def recreate_secret(self, old_secret):
        old_operator_name = old_secret["metadata"].get("labels", {}).get("app.kubernetes.io/part-of", None)
        if old_operator_name:
            old_secret_name = old_secret["metadata"]["name"]
            new_secret_name = old_secret_name.removeprefix(old_operator_name)
            new_secret_name = f"{self.operator_name}{new_secret_name}"
            current_secret = json.loads(self.run_k8s("get", "secret", new_secret_name, "-o", "json"))
            current_secret["data"] = old_secret["data"]
            self.run_k8s("apply", "-f", "-", stdin=json.dumps(current_secret).encode())

    def restore(self, name):
        if not name.endswith(".tar"):
            name = f"{name}.tar"
        with tarfile.open(name) as tar:
            for member in tar.getmembers():
                if member.name.endswith(".dump"):
                    with tar.extractfile(member) as dump:
                        content = dump.read()
                        self.recreate_db(content)
                elif member.name.endswith("_secret.json"):
                    with tar.extractfile(member) as dump:
                        content = json.loads(dump.read())
                        self.recreate_secret(content)
                elif member.type != tarfile.DIRTYPE:
                    print(f"Unknown backup file {member.name}", file=sys.stderr)

if __name__ == "__main__":
    default_name = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description="Utility to help backup and restore AWX instances deployed using the awx-operator.",
    )
    parser.add_argument(
        "command",
        choices=["backup", "restore"],
        help="The action to take",
    )
    parser.add_argument(
        "backup_name",
        nargs="?",
        default=default_name,
        help=f"The name of the backup, will be used to construct the backup filename <backup_name>.tar. Defaults to {default_name}",
    )
    parser.add_argument(
        "-n",
        "--namespace",
        default="awx",
        help="The namespace that was used when creating AWX using awx-operator",
    )
    args = parser.parse_args()

    deployment = Deployment(args.namespace)
    if args.command == "backup":
        deployment.backup(args.backup_name)
    elif args.command == "restore":
        deployment.restore(args.backup_name)

