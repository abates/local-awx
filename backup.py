#!/usr/bin/env python3

import base64
import json
import os
import shutil
import subprocess
import sys
import tarfile

class Deployment:
    def __init__(self, deployment):
        self.deployment = deployment
        self.db_configuration = json.loads(self.run_k8s("get", "secret", f"{self.deployment}-postgres-configuration", "-o", "jsonpath={.data}"))
        self.decode(self.db_configuration, *self.db_configuration.keys())

    @staticmethod
    def run_k8s(*cmd, stdin=None, stdout=None, stderr=None, decode=True):
        command = [
            "kubectl",
            "-n",
            "awx",
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

    @staticmethod
    def exec_k8s(pod, container, *cmd, stdin=None, stdout=None, stderr=None, decode=True):
        exec_cmd = ["exec"]
        if stdin:
            exec_cmd.append("-i")
        exec_cmd.extend([
            pod,
            "-c", container,
            "--",
            *cmd,
        ])
        return Deployment.run_k8s(*exec_cmd, stdin=stdin, stdout=stdout, stderr=stderr, decode=decode)

    def exec_db(self, cmd, *args, stdin=None, stdout=None, stderr=None, decode=True):
        return self.exec_k8s(
            f"{self.deployment}-postgres-13-0",
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
            if secret_name == f"{self.deployment}-postgres-configuration":
                continue

            with open(os.path.join(name, f"{secret_name}_secret.json"), "w") as secret_file:
                secret = self.run_k8s("get", "secret", secret_name, "-o", "json")
                print(secret, file=secret_file)

        with tarfile.open(f"{name}.tar", "w") as tar:
            tar.add(name)

        shutil.rmtree(name)

    def recreate_db(self, backup):
        # disconnect any clients
        #self.exec_db(
        #    "psql",
        #    "-d", "postgres",
        #    "-c", f"select pg_terminate_backend(pid) from pg_stat_activity where datname='{self.db_configuration['database']}'"
        #)

        # drop the database
        self.exec_db(
            "dropdb",
            "--force",
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
        #self.exec_db(
        #    "psql",
        #    "-d", self.db_configuration['database'],
        #    stdin=backup
        #)
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
        old_deployment = old_secret["metadata"].get("labels", {}).get("app.kubernetes.io/part-of", None)
        if old_deployment:
            old_secret_name = old_secret["metadata"]["name"]
            new_secret_name = old_secret_name.removeprefix(old_deployment)
            new_secret_name = f"{self.deployment}{new_secret_name}"
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
    if len(sys.argv) < 3:
        print(f"Usage: <{sys.argv[0]}> [backup|restore] [backup name] <deployment name>", file=sys.stderr)
        sys.exit(1)

    deployment_name = "awx-demo"
    if len(sys.argv) == 4:
        deployment_name = sys.argv[3]
    deployment = Deployment(deployment_name)
    if sys.argv[1] == "backup":
        deployment.backup(sys.argv[2])
    elif sys.argv[1] == "restore":
        deployment.restore(sys.argv[2])
    else:
        print(f"Unknown command {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

