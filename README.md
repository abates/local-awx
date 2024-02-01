# Starting AWX

1. Make sure `minikube` is installed
2. Start `minikube`
3. Deploy awx: `kubectl apply -k .`
4. Export the service: `minikube service -n awx awx-demo-service --url`

## Commands

List all of the pods in the awx namespace:
`kubectl -n awx get pods`

Get all of the pods managed by the operator:
`kubectl -n awx get pods -l "app.kubernetes.io/managed-by=awx-operator"`

View the deployment logs:
`kubectl -n awx logs -f deployments/awx-operator-controller-manager -c awx-manager`

Display services:
`kubectl -n awx get svc -l "app.kubernetes.io/managed-by=awx-operator"`

Get Admin password:
`kubectl -n awx get secret awx-demo-admin-password -o jsonpath="{.data.password}" | base64 --decode ; echo`

Allow External Access:
`sudo iptables -I INPUT -s 8080 -j ACCEPT`
`kubectl -n awx port-forward --address 0.0.0.0 services/awx-demo-service 8080:80`

Delete the deployment (operator name comes from the AWX resource metadata):
`kubectl -n awx delete awx <operator name>`
`kubectl delete namespaces awx`

